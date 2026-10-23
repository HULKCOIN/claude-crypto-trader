#!/usr/bin/env python3
"""crypto-entry: watch a candidate for the actual trigger bar."""
from __future__ import annotations

import argparse
import json
import sys

from _common import binance_klines, to_candles, write_signal


def check_breakout(candles, range_high: float) -> dict:
    recent = candles[-1]
    prev_vols = [c.v for c in candles[-20:-1]]
    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vol_ok = recent.v >= 1.5 * avg_vol
    close_above = recent.c > range_high
    wick_body = (recent.h - recent.c) / max(recent.h - recent.l, 1e-9)
    no_wick = wick_body < 0.4
    verdict = "go" if (close_above and vol_ok and no_wick) else "wait"
    return {
        "verdict": verdict,
        "entry_price": recent.c if verdict == "go" else None,
        "confirmation": {
            "close_above_range": close_above,
            "volume_confirmed": vol_ok,
            "no_wick_rejection": no_wick,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--setup", default="breakout", choices=["breakout"])
    p.add_argument("--range-high", type=float, required=True)
    p.add_argument("--timeframe", default="15m")
    args = p.parse_args()

    klines = binance_klines(args.symbol, interval=args.timeframe, limit=50)
    candles = to_candles(klines)
    result = check_breakout(candles, args.range_high)
    result.update({
        "source": "crypto-entry",
        "symbol": args.symbol,
        "setup": args.setup,
        "trigger_bar": candles[-1].open_time,
    })
    write_signal(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
