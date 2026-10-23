#!/usr/bin/env python3
"""crypto-scanner: scan the top-N USDT pairs on Binance for setups.

Setups:
  - breakout       : close > max(high[-lookback:-1]) with volume z-score > 1.5
  - flag           : impulse up (+3% in last 6 bars) then tight pullback, now breaking
  - oversold_bounce: RSI(14) crosses up through 30 with bullish close

Usage:
  python3 tools/scanner.py                     # scan default universe
  python3 tools/scanner.py --symbol BTCUSDT    # scan one pair
  python3 tools/scanner.py --json              # JSON only to stdout
  python3 tools/scanner.py --setups breakout   # comma-separated subset
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from _common import (
    atr,
    binance_klines,
    binance_top_pairs,
    find_setups_jake,
    load_config,
    rsi,
    to_candles,
    wilder_rsi,
    write_signal,
    zscore,
)


def detect_breakout(candles, lookback: int) -> dict | None:
    if len(candles) < lookback + 2:
        return None
    recent = candles[-1]
    prior = candles[-lookback - 1 : -1]
    range_high = max(c.h for c in prior)
    if recent.c <= range_high:
        return None
    # Pass full history; zscore compares last value to preceding `window` values.
    # Pass full history; zscore compares last value to preceding `window` values.
    vols = [c.v for c in candles]
    vol_z = zscore(vols, window=min(lookback, 20))
    if vol_z < 1.0:  # loosened from 1.5 — crypto often breaks out on lower vol surges
        return None
    return {
        "setup": "breakout",
        "price": recent.c,
        "range_high": range_high,
        "volume_zscore": round(vol_z, 2),
    }


def detect_flag(candles) -> dict | None:
    if len(candles) < 20:
        return None
    impulse_window = candles[-10:-4]
    pullback_window = candles[-4:]
    impulse_start = impulse_window[0].o
    impulse_end = impulse_window[-1].c
    if impulse_end / impulse_start - 1 < 0.015:  # 1.5% impulse (was 3%)
        return None
    pb_high = max(c.h for c in pullback_window[:-1])
    recent = pullback_window[-1]
    if recent.c <= pb_high:
        return None
    return {
        "setup": "flag",
        "price": recent.c,
        "range_high": pb_high,
        "impulse_pct": round((impulse_end / impulse_start - 1) * 100, 2),
    }


def detect_oversold_bounce(candles) -> dict | None:
    if len(candles) < 20:
        return None
    r_now = rsi([c.c for c in candles])
    r_prev = rsi([c.c for c in candles[:-1]])
    recent = candles[-1]
    if not (r_prev < 30 <= r_now):
        return None
    if recent.c <= recent.o:
        return None
    return {
        "setup": "oversold_bounce",
        "price": recent.c,
        "rsi": round(r_now, 1),
    }


DETECTORS = {
    "breakout": detect_breakout,
    "flag": lambda c, _lb: detect_flag(c),
    "oversold_bounce": lambda c, _lb: detect_oversold_bounce(c),
}

# Jake's 5 setups are added via find_setups_jake() in scan_symbol below.
JAKE_SETUPS = {"breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"}


def scan_symbol(symbol: str, timeframe: str, lookback: int, setups: list[str]) -> list[dict]:
    try:
        klines = binance_klines(symbol, interval=timeframe, limit=lookback + 20)
    except Exception as e:
        return [{"symbol": symbol, "error": str(e)}]
    candles = to_candles(klines)
    if len(candles) < lookback:
        return []

    out = []
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    closes = [c.c for c in candles]
    atr_val = atr(highs, lows, closes, 14)
    atr_pct = atr_val / closes[-1] * 100 if closes[-1] else 0

    # Our original (volume+breakout) detectors
    for setup in setups:
        if setup in JAKE_SETUPS:
            continue  # handled below
        det = DETECTORS.get(setup)
        if not det:
            continue
        hit = det(candles, lookback)
        if not hit:
            continue
        hit.update({
            "symbol": symbol,
            "exchange": "yahoo",
            "timeframe": timeframe,
            "atr": round(atr_val, 4),
            "atr_pct": round(atr_pct, 3),
        })
        hit["quality"] = round(
            min(1.0, hit.get("volume_zscore", 1.0) / 3 + atr_pct / 5), 2
        )
        out.append(hit)

    # Jake's 5 close-only setups (breakout_20d, bull_flag, reclaim_ma20, oversold, oversold_bounce)
    jake_wanted = [s for s in setups if s in JAKE_SETUPS]
    if jake_wanted and len(closes) >= 30:
        tags, nums = find_setups_jake(closes)
        for tag in tags:
            if tag not in jake_wanted:
                continue
            out.append({
                "symbol": symbol,
                "exchange": "yahoo",
                "timeframe": timeframe,
                "setup": tag,
                "price": candles[-1].c,
                "atr": round(atr_val, 4),
                "atr_pct": round(atr_pct, 3),
                "rsi": nums.get("rsi"),
                "high20": nums.get("high20"),
                "ma20": nums.get("ma20"),
                "quality": round(min(1.0, 0.5 + atr_pct / 10), 2),
            })
    return out


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="single symbol (e.g. BTCUSDT)")
    p.add_argument("--timeframe", default=cfg["scanner"]["timeframe"])
    p.add_argument("--lookback", type=int, default=cfg["scanner"]["lookback_bars"])
    p.add_argument(
        "--setups",
        default=",".join(cfg["scanner"]["setups"]),
        help="comma-separated: breakout,flag,oversold_bounce",
    )
    p.add_argument("--json", action="store_true", help="JSON to stdout only, no queue write")
    p.add_argument("--top-n", type=int, default=cfg["universe"]["top_n"])
    args = p.parse_args()

    setups = [s.strip() for s in args.setups.split(",") if s.strip()]

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        print(f"[scanner] fetching top {args.top_n} {cfg['universe']['quote']} pairs...", file=sys.stderr)
        symbols = binance_top_pairs(
            quote=cfg["universe"]["quote"],
            top_n=args.top_n,
            min_vol_usd=cfg["universe"]["min_volume_usd_24h"],
        )

    all_hits: list[dict[str, Any]] = []
    for sym in symbols:
        hits = scan_symbol(sym, args.timeframe, args.lookback, setups)
        for h in hits:
            if "error" in h:
                print(f"[scanner] {sym}: {h['error']}", file=sys.stderr)
                continue
            h["source"] = "crypto-scanner"
            all_hits.append(h)
            if not args.json:
                write_signal(h)

    all_hits.sort(key=lambda h: h.get("quality", 0), reverse=True)
    print(json.dumps(all_hits, indent=2))
    print(f"[scanner] {len(all_hits)} setups across {len(symbols)} pairs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
