#!/usr/bin/env python3
"""Opening Range Breakout Scanner für US- und DE-Aktien (Jake-Style).

Portiert Jake's /trader-scanner orb-Modus (day-trader-skill) für Aktien:
  1. Erste N Minuten der Session (default 15) = Opening Range (OR)
  2. OR-High = höchster High der ersten N Bars
  3. ORB-Hit = erster Bar der NACH der OR mit Close > OR-High schließt
  4. Push mit Trade-Idee (Entry=Close, Stop=OR-Low, Target=Entry+2× OR-Range)

Datenquelle: Yahoo Finance (v8 chart, 5-min bars). Nicht geoblockt.

Zwei Modi:
  --market us    NYSE/Nasdaq, Opening Range 9:30-9:45 ET  (15:30-15:45 MEZ)
  --market de    XETRA, Opening Range 9:00-9:15 MEZ

Universen:
  US:  Top-50 US-Aktien (SPY/QQQ-Komponenten) + bekannte Momentum-Names
  DE:  DAX40 (die liquidesten)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from _common import _yahoo_get, iso_now, write_signal  # noqa: E402


# ---------------------------------------------------------------- Universen

US_STOCKS = [
    # Mega-Cap Tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    # Big banks / Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C",
    # Healthcare giants
    "UNH", "JNJ", "LLY", "PFE", "ABBV",
    # Momentum / Trading-Favoriten
    "AMD", "INTC", "COIN", "MSTR", "PLTR", "SOFI", "RIVN", "NIO",
    # Energy / Commodities
    "XOM", "CVX", "COP",
    # Retail / Consumer
    "WMT", "COST", "HD", "DIS", "NKE", "MCD",
    # Industrials / Aerospace
    "BA", "CAT", "GE", "LMT", "RTX",
    # Semiconductor
    "QCOM", "TSM", "ASML", "MU",
    # Fintech / Payments
    "V", "MA", "PYPL", "SQ",
    # Pure momentum
    "GME", "AMC", "SMCI", "ARM",
]

# DAX40 — die liquidesten für ORB
DE_STOCKS = [
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "ADS.DE", "BAS.DE",
    "BAYN.DE", "BMW.DE", "DBK.DE", "DB1.DE", "DHL.DE", "DTG.DE", "ENR.DE",
    "FRE.DE", "HNR1.DE", "HEI.DE", "HEN3.DE", "IFX.DE", "LIN.DE", "MBG.DE",
    "MRK.DE", "MUV2.DE", "P911.DE", "PAH3.DE", "QIA.DE", "RHM.DE", "RWE.DE",
    "SHL.DE", "SRT3.DE", "SY1.DE", "VOW3.DE", "VNA.DE", "ZAL.DE",
]


# ---------------------------------------------------------------- Börsenzeiten

def _market_session(market: str) -> tuple[datetime, datetime, datetime]:
    """Returns (session_open, orb_end, session_close) als UTC datetimes."""
    now = datetime.now(timezone.utc)
    today = now.date()

    if market == "us":
        # NYSE: 9:30 ET = 13:30 UTC (sommer) oder 14:30 UTC (winter)
        # Vereinfachung: nutze 13:30 UTC (DST-check wäre zu komplex, user kontext ist Krypto 24/7)
        # Besser: Session via Yahoo-Meta ermitteln
        session_open = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=13, minute=30)
        orb_end = session_open + timedelta(minutes=15)
        session_close = session_open.replace(hour=20, minute=0)
    elif market == "de":
        # XETRA: 9:00 MEZ = 7:00 UTC (sommer) oder 8:00 UTC (winter)
        # Vereinfachung analog zu US
        session_open = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=7, minute=0)
        orb_end = session_open + timedelta(minutes=15)
        session_close = session_open.replace(hour=15, minute=30)
    else:
        raise ValueError(f"unknown market: {market}")

    return session_open, orb_end, session_close


# ---------------------------------------------------------------- Yahoo 5-min bars

def _fetch_5m(symbol: str, interval: str = "5m", range_: str = "1d") -> list[dict]:
    """Returns [{t, o, h, l, c, v}, ...] via Yahoo v8 chart."""
    try:
        raw = _yahoo_get(f"/v8/finance/chart/{symbol}?range={range_}&interval={interval}")
        doc = json.loads(raw)
    except Exception as e:
        print(f"[orb] fetch {symbol}: {e}", file=sys.stderr)
        return []
    res = (doc.get("chart") or {}).get("result") or []
    if not res:
        return []
    r0 = res[0]
    timestamps = r0.get("timestamp") or []
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []
    out = []
    for i, ts in enumerate(timestamps):
        if i >= len(closes) or closes[i] is None:
            continue
        out.append({
            "t": int(ts),
            "o": float(opens[i]) if opens[i] is not None else float(closes[i]),
            "h": float(highs[i]) if highs[i] is not None else float(closes[i]),
            "l": float(lows[i]) if lows[i] is not None else float(closes[i]),
            "c": float(closes[i]),
            "v": float(vols[i] or 0),
        })
    return out


# ---------------------------------------------------------------- ORB Logic

def check_orb(symbol: str, market: str, or_minutes: int = 15) -> dict | None:
    """Prüft ob Symbol heute einen ORB getriggert hat.
    Returns None wenn kein Breakout oder Daten unvollständig.
    """
    bars = _fetch_5m(symbol, interval="5m", range_="1d")
    if len(bars) < 4:
        return None

    session_open, orb_end, _ = _market_session(market)
    session_open_ts = int(session_open.timestamp())
    orb_end_ts = int(orb_end.timestamp())

    or_bars = [b for b in bars if session_open_ts <= b["t"] < orb_end_ts]
    post_bars = [b for b in bars if b["t"] >= orb_end_ts]
    if len(or_bars) < 2 or not post_bars:
        return None

    or_high = max(b["h"] for b in or_bars)
    or_low = min(b["l"] for b in or_bars)
    or_range = or_high - or_low
    or_avg_vol = sum(b["v"] for b in or_bars) / len(or_bars)

    # First post-OR bar that CLOSED above or_high
    for bar in post_bars:
        if bar["c"] > or_high:
            # Volume-Confirmation (mehr Volumen als OR-Durchschnitt)
            vol_confirm = bar["v"] >= or_avg_vol * 0.8
            entry = bar["c"]
            stop = or_low
            target = entry + 2 * or_range  # 2R target
            rr = (target - entry) / (entry - stop) if entry > stop else 0
            return {
                "symbol": symbol,
                "market": market,
                "or_high": round(or_high, 2),
                "or_low": round(or_low, 2),
                "or_range": round(or_range, 2),
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "rr": round(rr, 2),
                "trigger_time": datetime.fromtimestamp(bar["t"], tz=timezone.utc).strftime("%H:%M UTC"),
                "volume_confirmed": vol_confirm,
                "pct_above_or": round((entry / or_high - 1) * 100, 2),
            }
    return None


# ---------------------------------------------------------------- Telegram

def _tg(text: str, channel: str = "trades") -> bool:
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
        print(f"[orb] tg send failed: {e}", file=sys.stderr)
        return False


def format_orb(hit: dict) -> str:
    market_label = "🇺🇸 NYSE/Nasdaq" if hit["market"] == "us" else "🇩🇪 XETRA"
    vol_label = "✅ bestätigt" if hit["volume_confirmed"] else "⚠️ wenig Volumen"
    return (
        f"🚀 *OPENING RANGE BREAKOUT*\n"
        f"{market_label} — `{hit['symbol']}`\n\n"
        f"*📈 Trade-Plan:*\n"
        f"• Einstieg: *${hit['entry']}*  ({hit['pct_above_or']:+.2f}% über OR-High)\n"
        f"• 🛑 Stop-Loss: *${hit['stop']}* (OR-Low)\n"
        f"• 🎯 Take-Profit: *${hit['target']}* (2× OR-Range)\n"
        f"• R:R: *{hit['rr']}*\n\n"
        f"*📊 Opening Range (erste 15 Min):*\n"
        f"• High: ${hit['or_high']}\n"
        f"• Low: ${hit['or_low']}\n"
        f"• Range: ${hit['or_range']}\n"
        f"• Trigger: {hit['trigger_time']}\n"
        f"• Volumen: {vol_label}\n\n"
        f"💡 *Strategie:* Position schließen vor Börsenschluss oder bei Stop/Target\n"
        f"⚠️ Für US-Aktien: Alpaca-Account nötig für Live-Trading"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["us", "de"], required=True)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    universe = US_STOCKS if args.market == "us" else DE_STOCKS
    print(f"[orb] scanning {len(universe)} {args.market.upper()} stocks for ORB...")

    hits = []
    for sym in universe:
        time.sleep(0.3)  # polite Yahoo rate
        hit = check_orb(sym, args.market, args.or_minutes)
        if hit:
            hits.append(hit)
            print(f"[orb] HIT: {sym} entry={hit['entry']} target={hit['target']}")
            write_signal({"source": "stock-orb", **hit, "ts": iso_now()})

    # Top-5 nach R:R sortiert pushen
    hits.sort(key=lambda h: h["rr"], reverse=True)
    for hit in hits[:5]:
        msg = format_orb(hit)
        if args.dry_run:
            print(f"[dry-run] {msg[:100]}...")
        else:
            _tg(msg, channel="trades")

    print(f"[orb] {len(hits)} total ORB hits, top 5 pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
