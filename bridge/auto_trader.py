#!/usr/bin/env python3
"""Auto-trader: the 24/7 agent that watches the signal queue and acts.

Pipeline (per complete proposal assembled by hermes_bridge):
    1. require risk.verdict == PASS
    2. require timemachine.verdict == edge_positive (expected_r > 0.25)
    3. require sentiment NOT 'market_extended_long' when going long
    4. check pause flag — if present, skip
    5. place order on Binance testnet (or live if config says so + flagged)
    6. send Telegram alert

Run:
    python3 bridge/auto_trader.py

As a systemd service (recommended), see docs/24_7_DEPLOYMENT.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bridge.hermes_bridge import ProposalAssembler  # noqa: E402
from bridge.signal_queue import SignalQueue  # noqa: E402
from tools._common import BRIDGE_DIR, iso_now, load_config  # noqa: E402


PAUSE_FLAG = BRIDGE_DIR / "auto_trader.paused"
AUTO_LOG = BRIDGE_DIR / "auto_trades.jsonl"


def tg_notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").split(",")[0].strip()
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
        print(f"[auto] telegram notify failed: {e}", file=sys.stderr)


def policy(proposal: dict) -> tuple[bool, str]:
    tm = proposal.get("timemachine", {}) or {}
    risk = proposal.get("risk", {}) or {}
    sent = proposal.get("sentiment", {}) or {}
    plan = proposal.get("plan", {}) or {}

    if risk.get("verdict") != "PASS":
        return False, f"risk={risk.get('verdict')}"
    if tm.get("verdict") != "edge_positive":
        return False, f"timemachine={tm.get('verdict')} E[R]={tm.get('expected_r')}"
    if (tm.get("expected_r") or 0) < 0.25:
        return False, f"E[R]={tm.get('expected_r')} below threshold 0.25"
    if plan.get("side") == "long" and sent.get("verdict") == "market_extended_long":
        return False, "sentiment: market extended long"
    return True, "edge + risk ok"


def execute(proposal: dict) -> dict:
    cfg = load_config()
    plan = proposal["plan"]
    size = proposal["budget"]
    plan_path = BRIDGE_DIR / f"auto_plan_{plan['symbol']}_{int(time.time())}.json"
    plan_path.write_text(json.dumps(plan))
    size_path = BRIDGE_DIR / f"auto_size_{plan['symbol']}_{int(time.time())}.json"
    size_path.write_text(json.dumps(size))

    def py(script: str, *args: str) -> str:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / script), *args],
            capture_output=True, text=True, timeout=120, cwd=str(REPO),
        ).stdout

    # Explicit yes recorded by the auto-trader (not a human)
    py("broker.py", "create", "--plan", str(plan_path), "--size", str(size_path))
    py("broker.py", "approve", "--index", "1", "--reply", "yes")
    out = py("broker.py", "place", "--index", "1")

    row = {
        "ts": iso_now(),
        "mode": cfg["broker"]["mode"],
        "market": cfg["broker"].get("market", "spot"),
        "symbol": plan["symbol"],
        "side": plan["side"],
        "entry": plan["entry"],
        "stop": plan["stop"],
        "target": plan["target"],
        "qty": size.get("base_qty"),
        "notional_usd": size.get("notional_usd"),
        "edge_r": proposal.get("timemachine", {}).get("expected_r"),
        "broker_output": out[:500],
    }
    with open(AUTO_LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def main() -> int:
    assembler = ProposalAssembler()
    q = SignalQueue()
    print(f"[auto] watching {q.path}", file=sys.stderr)
    for signal in q.tail(poll_interval=2.0):
        if PAUSE_FLAG.exists():
            continue
        proposal = assembler.ingest(signal)
        if not proposal:
            continue
        ok, reason = policy(proposal)
        if not ok:
            print(f"[auto] skip {proposal['symbol']}: {reason}", file=sys.stderr)
            continue
        try:
            row = execute(proposal)
            tg_notify(
                f"🤖 *AUTO-TRADE* {row['symbol']} {row['side']}\n"
                f"entry: {row['entry']}  stop: {row['stop']}  target: {row['target']}\n"
                f"qty: {row['qty']}  risk: ${(float(row['qty'] or 0)) * (float(row['entry'] or 0) - float(row['stop'] or 0)):.2f}\n"
                f"edge E[R]: {row['edge_r']}  mode: {row['mode']}/{row['market']}"
            )
        except Exception as e:
            print(f"[auto] execute failed for {proposal['symbol']}: {e}", file=sys.stderr)
            tg_notify(f"⚠️ auto-trade failed for {proposal['symbol']}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
