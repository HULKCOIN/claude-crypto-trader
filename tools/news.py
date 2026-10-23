#!/usr/bin/env python3
"""crypto-news: FREE news from CoinGecko trending/updates + CoinDesk RSS.

No API key required. CoinGecko: 30 req/min free. CoinDesk RSS: unlimited.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from _common import http_get, iso_now, load_config, write_signal


COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
COINGECKO_UPDATES = "https://api.coingecko.com/api/v3/coins/{id}/status_updates"
COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINTELEGRAPH_RSS = "https://cointelegraph.com/rss"


class _RssItems(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._cur = None
        self._tag = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "item":
            self._cur = {}
        self._tag = tag
        self._text = []

    def handle_endtag(self, tag):
        if tag == "item" and self._cur is not None:
            self.items.append(self._cur)
            self._cur = None
        elif self._cur is not None and tag in ("title", "link", "pubdate", "description"):
            self._cur[tag] = "".join(self._text).strip()
        self._tag = None

    def handle_data(self, data):
        self._text.append(data)


def fetch_rss(url: str, limit: int = 15) -> list[dict]:
    try:
        req = Request(url, headers={"User-Agent": "claude-crypto-trader/0.1"})
        raw = urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[news] rss {url}: {e}", file=sys.stderr)
        return []
    # Normalize self-closing + case for the mini parser
    raw = re.sub(r"<(/?)([A-Za-z]+)", lambda m: f"<{m.group(1)}{m.group(2).lower()}", raw)
    p = _RssItems()
    try:
        p.feed(raw)
    except Exception:
        pass
    return p.items[:limit]


def fetch_coingecko_trending() -> list[dict]:
    try:
        data = http_get(COINGECKO_TRENDING)
    except Exception as e:
        print(f"[news] coingecko: {e}", file=sys.stderr)
        return []
    out = []
    for c in data.get("coins", [])[:10]:
        item = c.get("item", {})
        out.append({
            "symbol": item.get("symbol", "").upper(),
            "name": item.get("name"),
            "market_cap_rank": item.get("market_cap_rank"),
            "price_btc": item.get("price_btc"),
            "score": item.get("score"),
        })
    return out


HIGH_IMPACT = re.compile(
    r"\b(hack|exploit|sec\s+sues?|ban|etf|approv(ed|al)|listing|listed|halving|upgrade|fork|airdrop|launch(es|ed)?|partnership)\b",
    re.I,
)
NEGATIVE = re.compile(r"\b(hack|exploit|sues?|ban|crash|liquidat(ed|ion)|rug)\b", re.I)


def classify(title: str) -> tuple[str, str]:
    severity = "high" if HIGH_IMPACT.search(title or "") else "normal"
    sentiment = "negative" if NEGATIVE.search(title or "") else ("positive" if severity == "high" else "neutral")
    return severity, sentiment


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="filter to one symbol like BTC")
    args = p.parse_args()

    out = []

    # 1) Trending (free, no key)
    for coin in fetch_coingecko_trending():
        if args.symbol and coin.get("symbol") != args.symbol.upper():
            continue
        row = {
            "source": "crypto-news",
            "provider": "coingecko-trending",
            "symbol": coin.get("symbol"),
            "name": coin.get("name"),
            "severity": "normal",
            "sentiment": "neutral",
            "note": f"Trending #{coin.get('score')} rank {coin.get('market_cap_rank')}",
            "ts": iso_now(),
        }
        write_signal(row); out.append(row)

    # 2) CoinDesk RSS (free, unlimited)
    for item in fetch_rss(COINDESK_RSS, limit=10):
        title = item.get("title", "")
        if args.symbol and args.symbol.upper() not in title.upper():
            continue
        sev, sent = classify(title)
        row = {
            "source": "crypto-news",
            "provider": "coindesk",
            "symbol": args.symbol or "market",
            "headline": title,
            "url": item.get("link"),
            "published_at": item.get("pubdate"),
            "severity": sev,
            "sentiment": sent,
            "ts": iso_now(),
        }
        write_signal(row); out.append(row)

    # 3) Cointelegraph RSS (free, unlimited)
    for item in fetch_rss(COINTELEGRAPH_RSS, limit=10):
        title = item.get("title", "")
        if args.symbol and args.symbol.upper() not in title.upper():
            continue
        sev, sent = classify(title)
        row = {
            "source": "crypto-news",
            "provider": "cointelegraph",
            "symbol": args.symbol or "market",
            "headline": title,
            "url": item.get("link"),
            "published_at": item.get("pubdate"),
            "severity": sev,
            "sentiment": sent,
            "ts": iso_now(),
        }
        write_signal(row); out.append(row)

    print(json.dumps(out, indent=2))
    print(f"[news] {len(out)} items", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
