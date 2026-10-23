#!/usr/bin/env python3
"""crypto-news: pull breaking news from CryptoPanic and attach to signals."""
from __future__ import annotations

import argparse
import json
import os
import sys

from _common import http_get, load_config, write_signal


CRYPTOPANIC = "https://cryptopanic.com/api/v1/posts/"


def fetch(symbol: str | None, token: str | None, lookback_min: int) -> list[dict]:
    params = {"public": "true", "kind": "news", "filter": "hot"}
    if token:
        params["auth_token"] = token
    if symbol:
        params["currencies"] = symbol.replace("USDT", "").replace("USD", "")
    data = http_get(CRYPTOPANIC, params)
    return data.get("results", [])


def classify(title: str) -> tuple[str, str]:
    t = title.lower()
    high = ["hack", "exploit", "sec sues", "ban", "etf", "approved", "listing", "halving", "upgrade"]
    for kw in high:
        if kw in t:
            sent = "positive" if kw in ("etf", "approved", "listing", "halving", "upgrade") else "negative"
            return "high", sent
    return "normal", "neutral"


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol")
    p.add_argument("--lookback-min", type=int, default=cfg["news"]["lookback_minutes"])
    args = p.parse_args()

    token = os.environ.get(cfg["news"]["api_token_env"])
    try:
        posts = fetch(args.symbol, token, args.lookback_min)
    except Exception as e:
        print(f"[news] {e}", file=sys.stderr)
        return 1

    out = []
    for post in posts[:20]:
        severity, sentiment = classify(post.get("title", ""))
        row = {
            "source": "crypto-news",
            "symbol": args.symbol or "market",
            "headline": post.get("title"),
            "url": post.get("url"),
            "severity": severity,
            "sentiment": sentiment,
            "published_at": post.get("published_at"),
        }
        write_signal(row)
        out.append(row)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
