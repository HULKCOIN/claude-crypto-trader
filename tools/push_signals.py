#!/usr/bin/env python3
"""Push new high-quality signals from the queue to Telegram.

Dedupe via data/snapshots/last_push_ts.txt (committed by the workflow).
Only signals with ts > last_push_ts are considered. For scanner we add
a quality floor, for onchain a USD floor, for news we only push severity=high.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
QUEUE = REPO / "bridge" / "signal_queue.jsonl"
SNAPSHOT = REPO / "data" / "snapshots" / "latest.jsonl"
LAST_PUSH = REPO / "data" / "snapshots" / "last_push_ts.txt"


def read_last_ts() -> str:
    if LAST_PUSH.exists():
        return LAST_PUSH.read_text().strip() or "1970-01-01T00:00:00Z"
    return "1970-01-01T00:00:00Z"


def write_last_ts(ts: str) -> None:
    LAST_PUSH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PUSH.write_text(ts)


def tg_send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("[push] TELEGRAM_* env missing — skipping")
        return False
    data = urlencode({"chat_id": chat, "text": text[:4000], "parse_mode": "Markdown"}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=15) as r:
            res = json.loads(r.read().decode())
            return res.get("ok", False)
    except Exception as e:
        print(f"[push] send failed: {e}", file=sys.stderr)
        return False


def format_scanner(s: dict) -> str:
    rsi_line = f"RSI: {s.get('rsi')}\n" if s.get("rsi") is not None else ""
    return (
        f"🎯 *{s.get('symbol')}* — `{s.get('setup')}`\n"
        f"Price: ${s.get('price'):.6f}\n"
        f"Quality: {s.get('quality')}  ATR%: {s.get('atr_pct')}\n"
        f"{rsi_line}"
        f"Source: {s.get('exchange', 'yahoo')}"
    )


def format_onchain(s: dict) -> str:
    return (
        f"🐋 *Whale* — {str(s.get('chain', '')).upper()}\n"
        f"{s.get('symbol')}: ${s.get('amount_usd', 0):,.0f}\n"
        f"`{s.get('signal')}`"
    )


def format_news(s: dict) -> str:
    sev = s.get("severity", "normal")
    sym = s.get("symbol") or "market"
    icon = "🔥" if sev == "high" else "📰"
    hl = str(s.get("headline", ""))[:200]
    sentiment = s.get("sentiment", "neutral")
    sent_icon = {"positive": "🟢", "negative": "🔴"}.get(sentiment, "⚪")
    return f"{icon} *{sym}* {sent_icon}\n{hl}\n_{s.get('provider')}_"


FORMATTERS = {
    "crypto-scanner": format_scanner,
    "crypto-onchain": format_onchain,
    "crypto-news": format_news,
}


def should_push(sig: dict, args) -> bool:
    src = sig.get("source", "")
    if src == "crypto-scanner":
        return (sig.get("quality") or 0) >= args.min_quality
    if src == "crypto-onchain":
        return (sig.get("amount_usd") or 0) >= args.min_onchain_usd
    if src == "crypto-news":
        return sig.get("severity") == "high" if args.news_high_only else True
    return False


def load_signals() -> list[dict]:
    """Prefer live queue; fall back to snapshot (which is what GitHub Actions commits)."""
    path = QUEUE if QUEUE.exists() else SNAPSHOT
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-quality", type=float, default=0.4)
    p.add_argument("--min-onchain-usd", type=float, default=1_000_000)
    p.add_argument("--news-high-only", action="store_true", default=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    signals = load_signals()
    if not signals:
        print("[push] no signals")
        return 0

    cutoff = read_last_ts()
    print(f"[push] cutoff ts: {cutoff}")
    new = [s for s in signals if s.get("ts", "") > cutoff and should_push(s, args)]
    print(f"[push] {len(new)} new signals above thresholds (of {len(signals)} total)")

    max_ts = cutoff
    sent = 0
    for sig in new:
        src = sig.get("source", "")
        fmt = FORMATTERS.get(src)
        if not fmt:
            continue
        try:
            text = fmt(sig)
        except Exception as e:
            print(f"[push] format error on {src}: {e}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"[dry-run] would push: {text[:80]}...")
            sent += 1
        elif tg_send(text):
            sent += 1
            print(f"[push] sent {src}:{sig.get('symbol')}")
        ts = sig.get("ts", "")
        if ts > max_ts:
            max_ts = ts

    if sent > 0 and not args.dry_run and max_ts > cutoff:
        write_last_ts(max_ts)
        print(f"[push] advanced cutoff to {max_ts}")
    print(f"[push] {sent} notifications sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
