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
DEDUP_LOG = REPO / "data" / "snapshots" / "pushed_dedup.jsonl"
DEDUP_WINDOW_HOURS = 12  # selbe News/Whale max alle 12h — 2× pro Tag


import hashlib
import time as _time


def read_last_ts() -> str:
    if LAST_PUSH.exists():
        return LAST_PUSH.read_text().strip() or "1970-01-01T00:00:00Z"
    return "1970-01-01T00:00:00Z"


def write_last_ts(ts: str) -> None:
    LAST_PUSH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PUSH.write_text(ts)


def _signal_dedup_key(sig: dict) -> str:
    """Erzeugt stabilen Fingerprint pro News/Whale für Dedup."""
    src = sig.get("source", "")
    if src == "crypto-news":
        base = f"news|{sig.get('provider', '')}|{str(sig.get('headline', ''))[:80].lower()}"
    elif src == "crypto-onchain":
        base = f"whale|{sig.get('tx_hash', '')}"
    else:
        base = f"{src}|{sig.get('symbol', '')}|{sig.get('setup', sig.get('signal', ''))}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def _load_dedup_log() -> dict[str, float]:
    """Lädt {fingerprint: last_pushed_epoch}."""
    if not DEDUP_LOG.exists():
        return {}
    out = {}
    cutoff = _time.time() - DEDUP_WINDOW_HOURS * 3600 * 2  # Garbage-collect alte
    for line in DEDUP_LOG.read_text().splitlines():
        try:
            rec = json.loads(line)
            if rec.get("ts", 0) >= cutoff:
                out[rec["fp"]] = rec["ts"]
        except Exception:
            continue
    return out


def _save_dedup_log(log: dict[str, float]) -> None:
    DEDUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DEDUP_LOG, "w") as f:
        for fp, ts in log.items():
            f.write(json.dumps({"fp": fp, "ts": ts}) + "\n")


def _is_duplicate(sig: dict, dedup: dict[str, float]) -> bool:
    fp = _signal_dedup_key(sig)
    last = dedup.get(fp, 0)
    if _time.time() - last < DEDUP_WINDOW_HOURS * 3600:
        return True
    return False


def tg_send(text: str, channel: str = "trades") -> bool:
    """Send to the trades channel or the news channel.

    channel='trades' → TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (your main)
    channel='news'   → TELEGRAM_NEWS_BOT_TOKEN + TELEGRAM_NEWS_CHAT_ID
                       (falls nicht gesetzt, fällt auf 'trades' zurück)
    """
    if channel == "news":
        token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_NEWS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print(f"[push] TELEGRAM_* env missing for channel={channel} — skipping")
        return False
    data = urlencode({"chat_id": chat, "text": text[:4000],
                      "parse_mode": "Markdown",
                      "disable_web_page_preview": "true"}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=15) as r:
            res = json.loads(r.read().decode())
            return res.get("ok", False)
    except Exception as e:
        print(f"[push] send failed ({channel}): {e}", file=sys.stderr)
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
    frm = s.get("from_label") or s.get("from") or "Unknown"
    to = s.get("to_label") or s.get("to") or "Unknown"
    tx = s.get("tx_hash", "")
    bewertung = ""
    if "sell_pressure" in signal:
        bewertung = "\n📉 *Bedeutung:* Verkaufsdruck zu erwarten — Long-Positionen prüfen"
    elif "accumulation" in signal:
        bewertung = "\n📈 *Bedeutung:* Smart Money sammelt ein — Long-Setups zusätzlich bestätigt"
    elif "liquidity_in" in signal:
        bewertung = "\n📈 *Bedeutung:* Neue Liquidität im Markt (bullish 72h)"
    elif signal == "exchange_to_exchange":
        bewertung = "\n⚪ *Bedeutung:* Exchange-interner Transfer (OTC-Deal oder Rebalancing — neutral)"
    elif signal == "wallet_to_wallet":
        bewertung = "\n⚪ *Bedeutung:* Private Wallet-Transfer — Smart-Money-Umverteilung"
    tx_link = f"\n🔗 [Transaktion anzeigen](https://etherscan.io/tx/{tx})" if tx and chain == "ETHEREUM" else ""
    if tx and chain == "BITCOIN":
        tx_link = f"\n🔗 [Transaktion anzeigen](https://blockchair.com/bitcoin/transaction/{tx})"
    return (
        f"🐋 *Whale-Alarm — {chain} {symbol}*\n"
        f"Betrag: *${amount:,.0f}*\n"
        f"Von: `{frm}`\n"
        f"Nach: `{to}`\n"
        f"Signal: {erklaerung}"
        f"{bewertung}"
        f"{tx_link}"
    )


def format_news(s: dict) -> str:
    sev = s.get("severity", "normal")
    sym = s.get("symbol") or "Markt"
    icon = "🔥" if sev == "high" else "📰"
    hl_en = str(s.get("headline", ""))[:250].replace("<![CDATA[", "").replace("]]>", "").strip()
    # Übersetzung via Gemini (optional, cached)
    hl_de = ""
    try:
        from translate_news import translate
        de = translate(hl_en)
        if de and de.lower() != hl_en.lower():
            hl_de = f"\n🇩🇪 _{de}_"
    except Exception:
        pass
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
        f"{hl_en}"
        f"{hl_de}\n"
        f"_Quelle: {provider}_"
        f"{link_line}"
        f"{bewertung}"
    )


def should_push(sig: dict, args) -> bool:
    src = sig.get("source", "")
    # Scanner → pipeline_once.py pusht in Trade-Kanal
    if src == "crypto-onchain":
        return (sig.get("amount_usd") or 0) >= args.min_onchain_usd
    if src == "crypto-news":
        # News nur wenn News-Kanal konfiguriert → nicht spam im Trade-Kanal
        has_news_channel = bool(os.environ.get("TELEGRAM_NEWS_CHAT_ID"))
        if not has_news_channel:
            return False
        return sig.get("severity") == "high" if args.news_high_only else True
    return False


def channel_for(sig: dict) -> str:
    """Routet nach Kanal. News → 'news', Whales/Scanner → 'trades'."""
    if sig.get("source") == "crypto-news":
        return "news"
    return "trades"


# Scanner → pipeline_once.py pusht die vollständige Trade-Empfehlung in 'trades'.
# News → wenn News-Kanal gesetzt, geht in 'news' statt 'trades' (kein Spam).
# Whales → bleiben in 'trades' (action-relevant für offene Positionen).
FORMATTERS = {
    "crypto-onchain": format_onchain,
    "crypto-news": format_news,
}




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
    dedup = _load_dedup_log()
    new = [s for s in signals if s.get("ts", "") > cutoff and should_push(s, args)]
    print(f"[push] {len(new)} new candidate signals (of {len(signals)} total)")

    # Dedup-Filter: selbe News/Whale max alle 12h
    deduped = []
    skipped_dup = 0
    for sig in new:
        if _is_duplicate(sig, dedup):
            skipped_dup += 1
            continue
        deduped.append(sig)
    print(f"[push] {skipped_dup} duplicates blocked (12h window)")

    max_ts = cutoff
    sent = 0
    now_epoch = _time.time()
    for sig in deduped:
        src = sig.get("source", "")
        fmt = FORMATTERS.get(src)
        if not fmt:
            continue
        try:
            text = fmt(sig)
        except Exception as e:
            print(f"[push] format error on {src}: {e}", file=sys.stderr)
            continue
        ch = channel_for(sig)
        if args.dry_run:
            print(f"[dry-run] would push to {ch}: {text[:80]}...")
            sent += 1
        elif tg_send(text, channel=ch):
            sent += 1
            dedup[_signal_dedup_key(sig)] = now_epoch
            print(f"[push] sent {src}:{sig.get('symbol')} → {ch}")
        ts = sig.get("ts", "")
        if ts > max_ts:
            max_ts = ts

    if sent > 0 and not args.dry_run and max_ts > cutoff:
        write_last_ts(max_ts)
        _save_dedup_log(dedup)
        print(f"[push] advanced cutoff to {max_ts}, dedup saved")
    print(f"[push] {sent} notifications sent, {skipped_dup} duplicates skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
