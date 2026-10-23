#!/usr/bin/env python3
"""One-shot auto-trader pipeline for GitHub Actions cron.

For each new high-quality scanner hit since last run:
  1. exit.py        -> stop + target + plan.json
  2. timemachine.py -> Monte Carlo odds (reduced sims for CI speed)
  3. risk.py        -> account-wide risk gate
  4. budget.py      -> position sizing
  5. If policy passes (edge_positive + risk PASS):
       broker.py create + approve + place (paper mode by default)
       Push a Telegram alert with the full trade summary.

Dedupe via data/snapshots/last_pipeline_ts.txt committed by the workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools._common import BRIDGE_DIR, iso_now  # noqa: E402


QUEUE = BRIDGE_DIR / "signal_queue.jsonl"
SNAPSHOT = REPO / "data" / "snapshots" / "latest.jsonl"
LAST_TS_FILE = REPO / "data" / "snapshots" / "last_pipeline_ts.txt"
AUTO_LOG = BRIDGE_DIR / "auto_trades.jsonl"


def read_last_ts() -> str:
    if LAST_TS_FILE.exists():
        return LAST_TS_FILE.read_text().strip() or "1970-01-01T00:00:00Z"
    return "1970-01-01T00:00:00Z"


def write_last_ts(ts: str) -> None:
    LAST_TS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_TS_FILE.write_text(ts)


def tg_notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    try:
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urlencode({"chat_id": chat, "text": text[:4000], "parse_mode": "Markdown"}).encode(),
        ), timeout=15).read()
    except Exception as e:
        print(f"[auto] tg notify failed: {e}", file=sys.stderr)


def py(script: str, *args: str, timeout: int = 90) -> tuple[int, str, str]:
    r = subprocess.run(
        [sys.executable, str(REPO / "tools" / script), *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO),
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def load_signals() -> list[dict]:
    path = QUEUE if QUEUE.exists() else SNAPSHOT
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def process_one(sig: dict, sims: int) -> dict | None:
    symbol = sig.get("symbol")
    setup = sig.get("setup")
    price = sig.get("price")
    if not (symbol and price):
        return {"status": "skip", "reason": "missing symbol/price"}

    print(f"[auto] >>> {symbol} {setup} @ {price}")

    # 1. exit
    code, out, err = py("exit.py", "--symbol", symbol, "--entry", str(price))
    if code != 0:
        return {"status": "error", "step": "exit", "msg": (err or out)[:300]}
    try:
        plan = json.loads(out.splitlines()[-1] if "\n" in out else out)
    except Exception:
        plan_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
        try:
            plan = json.loads("\n".join(plan_lines) if plan_lines else out[out.index("{"):])
        except Exception:
            return {"status": "error", "step": "exit-parse", "msg": out[:300]}
    plan_path = plan.get("plan_path")
    if not plan_path:
        return {"status": "error", "step": "exit-no-plan-path"}
    plan_abs = str(REPO / plan_path) if not plan_path.startswith("/") else plan_path

    # 2. timemachine
    code, out, err = py("timemachine.py", "--plan", plan_abs, "--sims", str(sims))
    if code != 0:
        return {"status": "error", "step": "timemachine", "msg": (err or out)[:300]}
    try:
        tm = json.loads(out[out.index("{"):])
    except Exception:
        return {"status": "error", "step": "tm-parse", "msg": out[:300]}

    # 3. risk
    code, out, err = py("risk.py", "--plan", plan_abs)
    try:
        risk = json.loads(out[out.index("{"):]) if out.strip() else {}
    except Exception:
        risk = {}
    if code != 0 or risk.get("verdict") != "PASS":
        return {"status": "skip", "reason": f"risk={risk.get('verdict', 'FAIL')}",
                "plan": plan, "tm": tm, "risk": risk}

    # 4. budget
    code, out, err = py("budget.py", "--plan", plan_abs)
    if code != 0:
        return {"status": "error", "step": "budget", "msg": (err or out)[:300]}
    try:
        size = json.loads(out[out.index("{"):])
    except Exception:
        return {"status": "error", "step": "budget-parse"}

    # 5. policy
    er = tm.get("expected_r", 0)
    if er < 0.2:
        return {"status": "skip", "reason": f"E[R]={er} < 0.2",
                "plan": plan, "tm": tm, "risk": risk, "size": size}

    # 6. broker: create + approve + place (paper by default)
    size_path = BRIDGE_DIR / f"auto_size_{symbol}_{int(time.time())}.json"
    size_path.write_text(json.dumps(size))
    py("broker.py", "create", "--plan", plan_abs, "--size", str(size_path))
    py("broker.py", "approve", "--index", "1", "--reply", "yes")
    code, out, err = py("broker.py", "place", "--index", "1")
    broker_result = (out or err)[:400]

    row = {
        "ts": iso_now(),
        "symbol": symbol,
        "setup": setup,
        "entry": plan["entry"],
        "stop": plan["stop"],
        "target": plan["target"],
        "r_per_unit": plan["r_per_unit"],
        "qty": size.get("base_qty"),
        "notional_usd": size.get("notional_usd"),
        "edge_r": er,
        "p_target_first": tm.get("p_target_first"),
        "p_stop_first": tm.get("p_stop_first"),
        "broker_exit_code": code,
        "broker_result": broker_result,
    }
    AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    return {"status": "executed", **row}


def notify_executed(r: dict) -> None:
    tg_notify(
        f"🤖 *AUTO-TRADE* `{r['symbol']}` — {r['setup']}\n"
        f"Entry: {r['entry']}\n"
        f"Stop: {r['stop']}  🎯 Target: {r['target']}\n"
        f"Qty: {r['qty']}  Notional: ${r['notional_usd']}\n"
        f"E[R]: {r['edge_r']}  P(target): {r['p_target_first']}  P(stop): {r['p_stop_first']}\n"
        f"Mode: {os.environ.get('BROKER_MODE_HINT', 'paper')}"
    )


def notify_skipped(sig: dict, r: dict) -> None:
    tg_notify(
        f"🚫 *Skipped* `{sig.get('symbol')}` — {sig.get('setup')}\n"
        f"Reason: {r.get('reason')}"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-quality", type=float, default=0.5)
    p.add_argument("--sims", type=int, default=2000, help="Monte Carlo sims (CI-reduced)")
    p.add_argument("--max-per-run", type=int, default=5, help="cap trades per invocation")
    p.add_argument("--notify-skips", action="store_true")
    args = p.parse_args()

    signals = load_signals()
    if not signals:
        print("[auto] no signals")
        return 0

    cutoff = read_last_ts()
    scanner_hits = [
        s for s in signals
        if s.get("source") == "crypto-scanner"
        and (s.get("quality") or 0) >= args.min_quality
        and s.get("ts", "") > cutoff
    ]
    print(f"[auto] {len(scanner_hits)} new scanner hits above Q={args.min_quality} since {cutoff}")

    max_ts = cutoff
    executed = 0
    for sig in scanner_hits[: args.max_per_run]:
        try:
            r = process_one(sig, sims=args.sims)
        except Exception as e:
            print(f"[auto] exception on {sig.get('symbol')}: {e}", file=sys.stderr)
            continue
        if not r:
            continue
        print(f"[auto] {r.get('status')}: {r}")
        if r.get("status") == "executed":
            notify_executed(r)
            executed += 1
        elif r.get("status") == "skip" and args.notify_skips:
            notify_skipped(sig, r)
        ts = sig.get("ts", "")
        if ts > max_ts:
            max_ts = ts

    if max_ts > cutoff:
        write_last_ts(max_ts)
        print(f"[auto] advanced cutoff to {max_ts}")
    print(f"[auto] executed {executed} trades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
