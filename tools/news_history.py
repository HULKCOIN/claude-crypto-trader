#!/usr/bin/env python3
"""News-Historie + Markt-Impact-Analyse + Daily/Weekly Digest.

Jedes gepushte News-Item wird mit Zeitstempel + Preis-Snapshot gespeichert.
Danach kann man rückwirkend sehen: hat die Nachricht den Preis bewegt?

Aufrufe:
  python3 tools/news_history.py record       # merken: welche News → aktueller Preis
  python3 tools/news_history.py daily        # tägliche Zusammenfassung an News-Channel
  python3 tools/news_history.py weekly       # wöchentliche Impact-Statistik
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict, Counter
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request as _Request, urlopen as _urlopen
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from _common import binance_klines  # noqa: E402

QUEUE = REPO / "bridge" / "signal_queue.jsonl"
SNAPSHOT = REPO / "data" / "snapshots" / "latest.jsonl"
HISTORY = REPO / "data" / "snapshots" / "news_history.jsonl"


def _tg_send(text: str, channel: str = "news") -> bool:
    if channel == "news":
        token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_NEWS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        data = urlencode({"chat_id": chat, "text": text[:4000],
                          "parse_mode": "Markdown",
                          "disable_web_page_preview": "true"}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data), timeout=15).read()
        return True
    except Exception as e:
        print(f"[news-digest] send failed: {e}", file=sys.stderr)
        return False


def load_signals() -> list[dict]:
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


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    out = []
    for line in HISTORY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_history(records: list[dict]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def guess_symbol(headline: str) -> str | None:
    """Extrahiert bekannte Krypto-Symbole aus einer Headline."""
    txt = headline.upper()
    for sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
                "LTC", "SHIB", "ATOM", "UNI", "BCH", "NEAR", "APT", "ARB", "OP",
                "FIL", "INJ", "SUI", "SEI", "TIA", "FET", "RENDER", "HBAR", "ONDO",
                "PEPE", "WIF", "BONK", "JUP", "PYTH"]:
        if f" {sym}" in f" {txt} " or sym in ["BITCOIN", "ETHEREUM", "SOLANA"] and sym in txt:
            return sym
    for keyword, sym in [("BITCOIN", "BTC"), ("ETHEREUM", "ETH"), ("SOLANA", "SOL"),
                         ("CARDANO", "ADA"), ("DOGECOIN", "DOGE")]:
        if keyword in txt:
            return sym
    return None


def _fetch_price(symbol: str) -> float | None:
    """Aktuellen Preis holen."""
    try:
        kl = binance_klines(symbol + "USDT", interval="15m", limit=1)
        return float(kl[-1][4]) if kl else None
    except Exception:
        return None


def record() -> int:
    """Jede neue high-severity News mit aktuellem Preis speichern."""
    signals = load_signals()
    history = load_history()
    seen = {r.get("fingerprint") for r in history}

    new_count = 0
    for sig in signals:
        if sig.get("source") != "crypto-news":
            continue
        if sig.get("severity") != "high":
            continue
        headline = sig.get("headline", "")
        fp = f"{sig.get('provider')}|{headline[:80]}"
        if fp in seen:
            continue
        sym = guess_symbol(headline)
        price_at_news = _fetch_price(sym) if sym else None
        history.append({
            "fingerprint": fp,
            "ts": sig.get("ts"),
            "symbol": sym,
            "headline": headline[:200],
            "provider": sig.get("provider"),
            "sentiment": sig.get("sentiment"),
            "url": sig.get("url"),
            "price_at_news": price_at_news,
        })
        new_count += 1

    save_history(history)
    print(f"[news-history] recorded {new_count} new high-impact news")
    return 0


def _price_change_pct(symbol: str, since_ts: str, hours: int) -> float | None:
    """Preisänderung in % seit `since_ts` bis +hours Stunden danach."""
    try:
        kl = binance_klines(symbol + "USDT", interval="1h", limit=hours + 24)
    except Exception:
        return None
    try:
        since_epoch = int(time.mktime(time.strptime(since_ts, "%Y-%m-%dT%H:%M:%SZ"))) * 1000
    except Exception:
        return None
    bars = [k for k in kl if int(k[0]) >= since_epoch]
    if len(bars) < 2:
        return None
    start = float(bars[0][1])  # open at news
    end_idx = min(hours, len(bars) - 1)
    end = float(bars[end_idx][4])  # close N hours later
    return (end / start - 1) * 100 if start else None


def _gemini_momentum(context: dict) -> str:
    """Gemini erstellt Momentum-Prognose auf Basis der aggregierten Tagesdaten.
    Mit expliziter Unsicherheits-Angabe wenn Datenbasis dünn ist.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return ""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=" + key
    prompt = f"""Du bist Krypto-Marktanalyst. Erstelle eine kurze Momentum-Prognose für die
nächsten 24h basierend auf den folgenden aggregierten Tagesdaten.

TAGES-KONTEXT:
- High-Impact News gesamt: {context['news_high']} (positiv: {context['news_pos']}, negativ: {context['news_neg']})
- Whale-Transfers ≥$1M: {context['whales_big']}
- Whale-Volumen gesamt: ${context['whales_volume']:,.0f}
- Verkaufsdruck-Signale (Transfers zu Exchange): {context['sell_pressure']}
- Akkumulations-Signale (Transfers von Exchange): {context['accumulation']}
- Fear & Greed Index: {context['fng']} ({context['fng_label']})
- Top Headlines: {context['headlines'][:3]}

Antworte in genau diesem Format (deutsch, kurz):

📈 *Momentum-Prognose nächste 24h:*
[1-2 Sätze: steigend/fallend/seitwärts, mit konkretem Grund aus den Daten]

🎯 *Konfidenz:* [niedrig/mittel/hoch] — [kurze Begründung warum]

⚖️ *Positionierung:* [z.B. "mehr Longs als Shorts attraktiv" oder "defensiv, Shorts bevorzugen" oder "neutral abwarten"]

⚠️ *Unsicherheits-Faktoren:* [was spricht gegen die Prognose]

WICHTIG: Wenn die Datenbasis dünn ist (wenige News, wenige Whales), SAG ES OFFEN:
"Datenbasis zu dünn für belastbare Prognose."
Erfinde NIE Zahlen oder Trends die nicht in den Daten stehen."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }
    try:
        req = Request(url, data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode())
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[momentum] gemini error: {e}", file=sys.stderr)
        return ""


def daily() -> int:
    """Tägliche Zusammenfassung + Momentum-Prognose via Gemini."""
    signals = load_signals()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - 24 * 3600))
    recent = [s for s in signals if s.get("ts", "") >= cutoff]

    news = [s for s in recent if s.get("source") == "crypto-news"]
    news_high = [n for n in news if n.get("severity") == "high"]
    news_pos = [n for n in news_high if n.get("sentiment") == "positive"]
    news_neg = [n for n in news_high if n.get("sentiment") == "negative"]
    whales = [s for s in recent if s.get("source") == "crypto-onchain"]
    whales_big = [w for w in whales if (w.get("amount_usd") or 0) >= 1_000_000]
    sell_pressure = [w for w in whales if w.get("signal") == "potential_sell_pressure"]
    accumulation = [w for w in whales if w.get("signal") == "potential_accumulation"]
    sent = next((s for s in signals if s.get("source") == "crypto-sentiment"), {})

    headlines = [str(n.get("headline", "")) for n in news_high][:5]
    topics = "\n".join(f"• {h[:120]}" for h in headlines) or "_keine_"

    # Momentum-Prognose via Gemini
    momentum = _gemini_momentum({
        "news_high": len(news_high),
        "news_pos": len(news_pos),
        "news_neg": len(news_neg),
        "whales_big": len(whales_big),
        "whales_volume": sum(w.get("amount_usd", 0) or 0 for w in whales),
        "sell_pressure": len(sell_pressure),
        "accumulation": len(accumulation),
        "fng": sent.get("fear_greed_index", "?"),
        "fng_label": sent.get("fear_greed_label", "?"),
        "headlines": headlines,
    })

    msg = (
        f"📅 *TAGES-REPORT* ({time.strftime('%d.%m.%Y')})\n\n"
        f"*📰 News (24h):*\n"
        f"• Gesamt: {len(news)}\n"
        f"• High-Impact: {len(news_high)} (🟢 {len(news_pos)} / 🔴 {len(news_neg)})\n\n"
        f"*🐋 Whales (24h):*\n"
        f"• Transfers gesamt: {len(whales)}\n"
        f"• ≥ $1 Mio: {len(whales_big)}\n"
        f"• 📉 Verkaufsdruck (→Exchange): {len(sell_pressure)}\n"
        f"• 📈 Akkumulation (←Exchange): {len(accumulation)}\n"
        f"• Volumen: ${sum(w.get('amount_usd', 0) or 0 for w in whales):,.0f}\n\n"
        f"*📊 Sentiment:*\n"
        f"• Fear & Greed: {sent.get('fear_greed_index', '?')} ({sent.get('fear_greed_label', '?')})\n"
        f"• Verdict: {sent.get('verdict', '?')}\n\n"
        f"*🔥 Top-News heute:*\n{topics}\n\n"
        + (momentum if momentum else "_Momentum-Prognose nicht verfügbar (GEMINI_API_KEY fehlt)_")
    )
    _tg_send(msg, channel="news")
    print("[news-digest] daily sent")
    return 0


def weekly() -> int:
    """Wöchentliche Impact-Analyse: wie haben die News den Preis bewegt?"""
    history = load_history()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - 7 * 24 * 3600))
    recent = [h for h in history if h.get("ts", "") >= cutoff]
    if not recent:
        _tg_send("📊 *WOCHEN-REPORT:* Keine high-impact News diese Woche.", "news")
        return 0

    # Preis-Impact für jede News messen (+1h, +4h, +24h)
    impacts = []
    for h in recent:
        sym = h.get("symbol")
        if not sym or not h.get("price_at_news"):
            continue
        for horizon_hours in (1, 4, 24):
            change = _price_change_pct(sym, h["ts"], horizon_hours)
            if change is not None:
                impacts.append({
                    "sym": sym, "headline": h["headline"][:80],
                    "sentiment": h.get("sentiment"), "horizon": horizon_hours,
                    "change_pct": change,
                })

    # Aggregate pro Sentiment × Horizon
    by_sent_h = defaultdict(list)
    for i in impacts:
        by_sent_h[(i["sentiment"], i["horizon"])].append(i["change_pct"])

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    rows = []
    for h in (1, 4, 24):
        pos = avg(by_sent_h.get(("positive", h), []))
        neg = avg(by_sent_h.get(("negative", h), []))
        rows.append(f"  +{h}h: 🟢 {pos:+.2f}% | 🔴 {neg:+.2f}%")

    # Top-Performer
    biggest_moves = sorted(impacts, key=lambda i: abs(i["change_pct"]), reverse=True)[:5]
    moves_block = "\n".join(
        f"• `{m['sym']}` {m['change_pct']:+.2f}% (+{m['horizon']}h): _{m['headline'][:60]}_"
        for m in biggest_moves
    )

    msg = (
        f"📊 *WOCHEN-REPORT* ({time.strftime('%d.%m.%Y')})\n"
        f"_News → Markt-Reaktion der letzten 7 Tage_\n\n"
        f"*Analysierte High-Impact News:* {len(recent)}\n\n"
        f"*⚡ Durchschnittliche Reaktion:*\n"
        + "\n".join(rows) +
        f"\n\n*🔥 Größte Bewegungen:*\n{moves_block or '_keine_'}\n\n"
        f"_Positive Zahlen = Preis stieg nach News, Negative = Preis fiel_"
    )
    _tg_send(msg, channel="news")
    print("[news-digest] weekly sent")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["record", "daily", "weekly"])
    args = p.parse_args()
    if args.mode == "record":
        return record()
    if args.mode == "daily":
        return daily()
    if args.mode == "weekly":
        return weekly()
    return 1


if __name__ == "__main__":
    sys.exit(main())
