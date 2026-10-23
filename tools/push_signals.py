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


SIGNAL_ERKLAERUNG = {
    "potential_sell_pressure": "⚠️ Könnte Verkaufsdruck geben (Whale legt ab auf Exchange)",
    "potential_accumulation": "✅ Smart Money sammelt ein (Abhebung von Exchange)",
    "stablecoin_mint_liquidity_in": "💰 Neue Liquidität im Markt (Stablecoin wird gedruckt)",
    "large_btc_transfer": "📊 Großer BTC-Transfer (noch unklar wohin)",
    "large_eth_transfer": "📊 Großer ETH-Transfer (noch unklar wohin)",
}


def format_onchain(s: dict) -> str:
    amount = s.get("amount_usd", 0) or 0
    chain = str(s.get("chain", "")).upper()
    symbol = s.get("symbol", "?")
    signal = s.get("signal", "")
    erklaerung = SIGNAL_ERKLAERUNG.get(signal, signal)
    bewertung = ""
    if "sell_pressure" in signal:
        bewertung = "\n📉 *Bedeutung:* Positionen schließen oder short erwägen"
    elif "accumulation" in signal:
        bewertung = "\n📈 *Bedeutung:* Long-Setup zusätzlich bestätigt"
    elif "liquidity_in" in signal:
        bewertung = "\n📈 *Bedeutung:* Bullish für den ganzen Markt (72h-Horizont)"
    return (
        f"🐋 *Whale-Alarm — {chain} {symbol}*\n"
        f"Betrag: *${amount:,.0f}*\n"
        f"Signal: {erklaerung}"
        f"{bewertung}"
    )


def format_news(s: dict) -> str:
    sev = s.get("severity", "normal")
    sym = s.get("symbol") or "Markt"
    icon = "🔥" if sev == "high" else "📰"
    hl = str(s.get("headline", ""))[:250]
    sentiment = s.get("sentiment", "neutral")
    sent_label = {"positive": "🟢 positiv", "negative": "🔴 negativ"}.get(sentiment, "⚪ neutral")
    provider = s.get("provider", "unbekannt")
    url = s.get("url", "")
    link_line = f"\n[→ Artikel öffnen]({url})" if url else ""
    bewertung = ""
    if sev == "high" and sentiment == "positive":
        bewertung = "\n💡 *Einschätzung:* Könnte Preis-Impuls auslösen — Watchlist beobachten"
    elif sev == "high" and sentiment == "negative":
        bewertung = "\n⚠️ *Einschätzung:* Negativ-Impact möglich — Long-Positionen prüfen"
    return (
        f"{icon} *{sym}* ({sent_label})\n"
        f"{hl}\n"
        f"_Quelle: {provider}_"
        f"{link_line}"
        f"{bewertung}"
    )


# Scanner → pipeline_once.py pusht die vollständige Trade-Empfehlung.
# News → NICHT mehr standalone gepusht (zu viel Rauschen). Gemini bekommt
#        sie weiter als Kontext für Trade-Entscheidungen.
# Whales → bleiben, da action-relevant (Verkaufsdruck / Akkumulation beeinflusst
#          offene Positionen).
FORMATTERS = {
    "crypto-onchain": format_onchain,
}


def should_push(sig: dict, args) -> bool:
    src = sig.get("source", "")
    # Scanner → pipeline_once.py pusht
    # News → nicht mehr standalone (zu viel Rauschen, siehe FORMATTERS Kommentar)
    if src == "crypto-onchain":
        return (sig.get("amount_usd") or 0) >= args.min_onchain_usd
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
