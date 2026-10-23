#!/usr/bin/env python3
"""Monitors Binance announcements for new coin listings.

New listing on Binance → historisch oft +20-50% in den ersten Stunden.
Signal für den Trade-Kanal.

Datenquelle: Binance Announcements RSS
  https://www.binance.com/en/support/announcement/c-48
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from _common import iso_now, write_signal  # noqa: E402

SEEN = REPO / "data" / "snapshots" / "seen_listings.jsonl"

BINANCE_ANNOUNCE_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?catalogId=48&pageNo=1&pageSize=15"
BINANCE_ARTICLE_URL = "https://www.binance.com/en/support/announcement/{code}"


def _tg_send(text: str, channel: str = "trades") -> bool:
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
        print(f"[listings] tg failed: {e}", file=sys.stderr)
        return False


def _load_seen() -> set:
    if not SEEN.exists():
        return set()
    out = set()
    for line in SEEN.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.add(json.loads(line).get("code"))
            except Exception:
                pass
    return out


def _save_seen(seen: set) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN, "w") as f:
        for code in seen:
            f.write(json.dumps({"code": code, "ts": iso_now()}) + "\n")


LISTING_PATTERNS = [
    re.compile(r"(?i)Binance Will List\s+([A-Z0-9]{2,10})"),
    re.compile(r"(?i)Will List\s+([A-Z0-9]{2,10})\s*\("),
    re.compile(r"(?i)Lists?\s+([A-Z0-9]{2,10})\s*\("),
    re.compile(r"(?i)Introducing\s+([A-Z0-9]{2,10})"),
]


def _extract_symbol(title: str) -> str | None:
    for pat in LISTING_PATTERNS:
        m = pat.search(title)
        if m:
            sym = m.group(1).upper()
            if sym in ("ETF", "USD", "USDT", "USDC"):  # false positives
                continue
            return sym
    return None


def _format_listing(title: str, url: str, symbol: str | None) -> str:
    sym_block = f"\n*Symbol:* `{symbol}`" if symbol else ""
    verdict = ""
    if symbol:
        verdict = (
            "\n\n💡 *Trade-Idee:*\n"
            "• Historisch: neue Binance-Listings oft +20-50% in ersten 2-6h\n"
            "• Entry sofort nach offiziellem Trading-Start\n"
            "• TP: +15% / SL: -10%\n"
            "• ⚠️ Hohe Volatilität, kleine Position (max 1% Konto)"
        )
    return (
        f"🚀 *NEUES BINANCE-LISTING*\n"
        f"{title[:200]}"
        f"{sym_block}"
        f"\n🔗 [Ankündigung lesen]({url})"
        f"{verdict}"
    )


def main() -> int:
    try:
        req = Request(BINANCE_ANNOUNCE_URL,
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        raw = urlopen(req, timeout=15).read().decode()
        data = json.loads(raw)
    except Exception as e:
        print(f"[listings] fetch error: {e}", file=sys.stderr)
        return 1

    articles = (data.get("data") or {}).get("articles") or []
    if not articles:
        print("[listings] no articles found (response shape may have changed)", file=sys.stderr)
        return 0

    seen = _load_seen()
    new_count = 0
    for art in articles:
        code = art.get("code")
        title = art.get("title", "")
        if not code or code in seen:
            continue
        # Nur Listings (keine Delistings, Updates, etc.)
        if not any(k in title.lower() for k in ("will list", "lists ", "introducing", "launches ")):
            seen.add(code)
            continue
        symbol = _extract_symbol(title)
        url = BINANCE_ARTICLE_URL.format(code=code)
        msg = _format_listing(title, url, symbol)
        if _tg_send(msg, channel="trades"):
            new_count += 1
            print(f"[listings] NEW: {title[:80]}")
            write_signal({
                "source": "crypto-listing",
                "symbol": symbol,
                "title": title,
                "url": url,
                "ts": iso_now(),
            })
        seen.add(code)

    _save_seen(seen)
    print(f"[listings] {new_count} new listings pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
