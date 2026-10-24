#!/usr/bin/env python3
"""crypto-exit: set ATR-based stop and target BEFORE entry; write plan.json."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from _common import PLANS_DIR, atr, binance_klines, to_candles, write_signal


def main() -> int:
    # Defaults können via ENV überschrieben werden damit Pipeline nicht
    # jedesmal Flags setzen muss. STOP_ATR_MULT=2.5 / TARGET_ATR_MULT=4.5
    # gibt deutlich breitere Stops → raus aus dem 15m-Noise.
    default_stop = float(os.environ.get("STOP_ATR_MULT", "2.5"))
    default_target = float(os.environ.get("TARGET_ATR_MULT", "4.5"))

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--side", default="long", choices=["long", "short"])
    p.add_argument("--stop-atr-mult", type=float, default=default_stop)
    p.add_argument("--target-atr-mult", type=float, default=default_target)
    p.add_argument("--timeframe", default="15m")
    args = p.parse_args()

    klines = binance_klines(args.symbol, interval=args.timeframe, limit=50)
    candles = to_candles(klines)
    a = atr([c.h for c in candles], [c.l for c in candles], [c.c for c in candles], 14)

    if args.side == "long":
        stop = args.entry - args.stop_atr_mult * a
        target = args.entry + args.target_atr_mult * a
    else:
        stop = args.entry + args.stop_atr_mult * a
        target = args.entry - args.target_atr_mult * a

    r_per_unit = abs(args.entry - stop)
    plan = {
        "source": "crypto-exit",
        "symbol": args.symbol,
        "side": args.side,
        "entry": round(args.entry, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "atr_14": round(a, 8),
        "r_per_unit": round(r_per_unit, 8),
        "reward_risk": round(abs(target - args.entry) / r_per_unit, 2) if r_per_unit else None,
        "invalidation_reason": f"{args.stop_atr_mult}× ATR from entry",
    }

    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = PLANS_DIR / f"{args.symbol}_{int(time.time())}.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    plan["plan_path"] = str(plan_path.relative_to(Path(__file__).resolve().parent.parent))

    write_signal(plan)
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
