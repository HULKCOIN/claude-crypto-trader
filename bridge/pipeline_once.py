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

try:
    from tools.gemini_decider import review_trade as gemini_review, format_gemini_message
except Exception:
    gemini_review = None
    format_gemini_message = None


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


def _parse_json(text: str) -> dict | None:
    """Robust parse: try whole output, then from first { to end."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    if "{" in text:
        try:
            return json.loads(text[text.index("{"):])
        except Exception:
            pass
    return None


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
    plan = _parse_json(out)
    if not plan:
        return {"status": "error", "step": "exit-parse", "msg": out[:500]}
    plan_path = plan.get("plan_path")
    if not plan_path:
        return {"status": "error", "step": "exit-no-plan-path", "plan": plan}
    plan_abs = str(REPO / plan_path) if not plan_path.startswith("/") else plan_path

    # 2. timemachine
    code, out, err = py("timemachine.py", "--plan", plan_abs, "--sims", str(sims))
    if code != 0:
        return {"status": "error", "step": "timemachine", "msg": (err or out)[:300]}
    tm = _parse_json(out)
    if not tm:
        return {"status": "error", "step": "tm-parse", "msg": out[:500]}

    # 3. risk
    code, out, err = py("risk.py", "--plan", plan_abs)
    risk = _parse_json(out) or {}
    if code != 0 or risk.get("verdict") != "PASS":
        return {"status": "skip", "reason": f"risk={risk.get('verdict', 'FAIL')}",
                "plan": plan, "tm": tm, "risk": risk}

    # 4. budget
    code, out, err = py("budget.py", "--plan", plan_abs)
    if code != 0:
        return {"status": "error", "step": "budget", "msg": (err or out)[:300]}
    size = _parse_json(out)
    if not size:
        return {"status": "error", "step": "budget-parse", "msg": out[:500]}

    # 5. policy — Rules-Decider zuerst
    er = tm.get("expected_r", 0)
    if er < 0.2:
        return {"status": "skip", "reason": f"E[R]={er} < 0.2 (Edge zu dünn)",
                "plan": plan, "tm": tm, "risk": risk, "size": size}

    # 5b. Optional: Gemini-LLM-Review als zweite Meinung (nur wenn GEMINI_API_KEY)
    gemini_verdict = None
    if gemini_review and os.environ.get("GEMINI_API_KEY"):
        proposal_bundle = {
            "scanner_hit": sig,
            "plan": plan,
            "timemachine": tm,
            "size": size,
            "risk": risk,
        }
        try:
            gemini_verdict = gemini_review(proposal_bundle)
            print(f"[auto] gemini verdict: {gemini_verdict.get('verdict')} "
                  f"({gemini_verdict.get('confidence')}) — {gemini_verdict.get('reasoning')}")
            # Push Gemini-Review separately
            if format_gemini_message:
                from urllib.parse import urlencode
                from urllib.request import Request, urlopen
                token = os.environ.get("TELEGRAM_BOT_TOKEN")
                chat = os.environ.get("TELEGRAM_CHAT_ID")
                if token and chat:
                    msg = format_gemini_message(plan["symbol"], gemini_verdict)
                    try:
                        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                        data=urlencode({"chat_id": chat, "text": msg[:4000],
                                                        "parse_mode": "Markdown"}).encode()),
                                timeout=15).read()
                    except Exception as e:
                        print(f"[auto] gemini notify failed: {e}", file=sys.stderr)
            # If Gemini says SKIP → skip (LLM-Veto)
            if gemini_verdict.get("verdict") == "SKIP":
                return {"status": "skip",
                        "reason": f"Gemini SKIP: {gemini_verdict.get('reasoning', '')}",
                        "plan": plan, "tm": tm, "risk": risk, "size": size,
                        "gemini": gemini_verdict}
        except Exception as e:
            print(f"[auto] gemini review error: {e}", file=sys.stderr)

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
    return {"status": "executed", "plan": plan, "tm": tm, "size": size, "risk": risk, **row}


SETUP_ERKLAERUNG = {
    "reclaim_ma20": "20-Tage-Durchschnitt zurückerobert (Trendwende aufwärts)",
    "breakout_20d": "Ausbruch über 20-Tage-Hoch (starker Aufwärts-Impuls)",
    "bull_flag": "Bull-Flag nach +10% Impuls (Fortsetzung erwartet)",
    "oversold": "Überverkauft (RSI < 30, kurzfristige Erholung möglich)",
    "oversold_bounce": "Erholung aus überverkauftem Zustand (Reversal-Signal)",
    "breakout": "Breakout über jüngstes Hoch mit Volumen-Surge",
    "flag": "Flaggen-Formation (bullische Fortsetzung)",
}


def _pct(a: float, b: float) -> str:
    if not b:
        return "—"
    return f"{(a / b - 1) * 100:+.2f}%"


def _format_trade_message(sig: dict, plan: dict, tm: dict, size: dict | None,
                           risk: dict | None, status: str, reason: str = "") -> str:
    """Vollständige deutsche Trade-Empfehlung mit allen Infos."""
    symbol = sig["symbol"]
    setup = sig.get("setup", "?")
    erklaerung = SETUP_ERKLAERUNG.get(setup, setup)
    entry = plan["entry"]
    stop = plan["stop"]
    target = plan["target"]
    rr = plan.get("reward_risk", 0)
    p_target = tm.get("p_target_first", 0) * 100
    p_stop = tm.get("p_stop_first", 0) * 100
    expected_r = tm.get("expected_r", 0)
    hist_wr = tm.get("historical_win_rate")
    hist_wr_str = f"{hist_wr * 100:.0f}%" if hist_wr is not None else "n/a"

    # Status-Icon + Empfehlung
    if status == "executed":
        kopf = f"🤖 *AUTO-TRADE GEÖFFNET*\n`{symbol}` ({setup})"
        empfehlung = "✅ *Position wurde eröffnet* (Demo/Paper-Modus)"
    elif status == "skipped":
        kopf = f"🚫 *SIGNAL ÜBERSPRUNGEN*\n`{symbol}` ({setup})"
        empfehlung = f"❌ *Grund:* {reason}\n💡 *Empfehlung:* NICHT traden — Edge zu schwach"
    elif status == "proposal":
        kopf = f"🎯 *HANDELS-SIGNAL*\n`{symbol}` ({setup})"
        if expected_r > 0.3:
            empfehlung = f"⭐⭐⭐ *EMPFEHLUNG: KAUFEN* — guter Edge"
        elif expected_r > 0.1:
            empfehlung = f"⭐⭐ *EMPFEHLUNG: VORSICHTIG KAUFEN* — mittlerer Edge"
        else:
            empfehlung = f"⭐ *EMPFEHLUNG: BEOBACHTEN* — Edge marginal"
    else:
        kopf = f"ℹ️ `{symbol}` ({setup})"
        empfehlung = ""

    size_block = ""
    if size:
        notional = size.get("notional_usd", 0)
        qty = size.get("base_qty", 0)
        risiko = size.get("dollar_risk_at_stop", 0)
        size_block = (
            f"\n*📊 Position:*\n"
            f"• Menge: `{qty:.6f} {symbol.replace('USDT', '').replace('USD', '')}`\n"
            f"• Einsatz: *${notional:,.2f}*\n"
            f"• Max. Verlust bei Stop: *${risiko:,.2f}*"
        )

    risk_block = ""
    if risk and status != "proposal":
        if risk.get("verdict") == "PASS":
            risk_block = "\n✅ Risk-Check: bestanden"
        else:
            risk_block = f"\n⚠️ Risk-Check: {risk.get('verdict')}"

    return (
        f"{kopf}\n"
        f"_{erklaerung}_\n\n"
        f"*📈 Trade-Plan:*\n"
        f"• Einstieg: *${entry:,.6f}*\n"
        f"• 🛑 Stop-Loss: *${stop:,.6f}* ({_pct(stop, entry)})\n"
        f"• 🎯 Take-Profit: *${target:,.6f}* ({_pct(target, entry)})\n"
        f"• R:R Verhältnis: *{rr:.2f}*"
        f"{size_block}\n\n"
        f"*🎲 Wahrscheinlichkeiten* (2.000 Monte-Carlo-Simulationen):\n"
        f"• Target zuerst: *{p_target:.1f}%*\n"
        f"• Stop zuerst: *{p_stop:.1f}%*\n"
        f"• Erwartungswert: *{expected_r:+.2f} R*\n"
        f"• Historische Trefferquote: *{hist_wr_str}*"
        f"{risk_block}\n\n"
        f"{empfehlung}"
    )


def notify_executed(sig: dict, plan: dict, tm: dict, size: dict, risk: dict) -> None:
    tg_notify(_format_trade_message(sig, plan, tm, size, risk, "executed"))


def notify_skipped(sig: dict, r: dict) -> None:
    plan = r.get("plan")
    tm = r.get("tm")
    if plan and tm:
        tg_notify(_format_trade_message(sig, plan, tm, r.get("size"), r.get("risk"),
                                        "skipped", r.get("reason", "unbekannt")))
    else:
        tg_notify(
            f"🚫 *Signal übersprungen* `{sig.get('symbol')}` ({sig.get('setup')})\n"
            f"Grund: {r.get('reason', 'unbekannt')}"
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-quality", type=float, default=0.5)
    p.add_argument("--sims", type=int, default=2000, help="Monte Carlo sims (CI-reduced)")
    p.add_argument("--max-per-run", type=int, default=5, help="cap trades per invocation")
    p.add_argument("--notify-skips", action="store_true", default=True,
                   help="auch gesendete Signale pushen (mit SKIP-Begründung)")
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
        print(f"[auto] {r.get('status')}: {sig.get('symbol')} — {r.get('reason', '')}")
        if r.get("status") == "executed":
            notify_executed(sig, r["plan"], r["tm"], r["size"], r["risk"])
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
