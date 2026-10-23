#!/usr/bin/env python3
"""crypto-sentiment: Fear & Greed index + Binance funding/long-short."""
from __future__ import annotations

import argparse
import json
import sys

from _common import http_get, load_config, write_signal


FNG = "https://api.alternative.me/fng/"
BINANCE_FAPI = "https://fapi.binance.com"


def fetch_fear_greed() -> dict:
    data = http_get(FNG, {"limit": 1})
    row = data.get("data", [{}])[0]
    return {
        "fear_greed_index": int(row.get("value", 50)),
        "fear_greed_label": row.get("value_classification"),
    }


def fetch_funding(symbol: str) -> dict:
    try:
        rate = http_get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": symbol})
        return {"funding_rate_8h": float(rate.get("lastFundingRate", 0))}
    except Exception:
        return {}


def fetch_long_short(symbol: str) -> dict:
    try:
        rows = http_get(f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
                        {"symbol": symbol, "period": "1h", "limit": 1})
        if rows:
            return {"long_short_ratio": float(rows[0]["longShortRatio"])}
    except Exception:
        pass
    return {}


def verdict(d: dict) -> str:
    fng = d.get("fear_greed_index", 50)
    fr = d.get("funding_rate_8h", 0)
    if fng > 75 and fr > 0.0005:
        return "market_extended_long"
    if fng < 25 and fr < 0:
        return "market_capitulated"
    return "neutral"


def main() -> int:
    _ = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    args = p.parse_args()

    result = {"source": "crypto-sentiment", "symbol": args.symbol}
    result.update(fetch_fear_greed())
    result.update(fetch_funding(args.symbol))
    result.update(fetch_long_short(args.symbol))
    result["verdict"] = verdict(result)
    write_signal(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
