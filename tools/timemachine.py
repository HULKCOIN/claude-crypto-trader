#!/usr/bin/env python3
"""crypto-timemachine: Monte Carlo replay of a trade plan.

Takes entry/stop/target and bootstraps the pair's own recent log-returns to
replay the rest of the session N times. Returns P(target first), P(stop
first), expected R, and the actual historical hit rate for the same rule.

Usage:
  python3 tools/timemachine.py --symbol BTCUSDT --entry 68355 --stop 68032 --target 68894
  python3 tools/timemachine.py --plan bridge/plans/BTCUSDT_1729658400.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

from _common import binance_klines, load_config, to_candles, write_signal


def simulate(
    returns: list[float],
    entry: float,
    stop: float,
    target: float,
    bars: int,
    sims: int,
    side: str = "long",
    seed: int | None = None,
) -> dict:
    rng = random.Random(seed)
    n_target = n_stop = n_neither = 0
    final_r = 0.0
    r_per_unit = abs(entry - stop) if side == "long" else abs(stop - entry)
    if r_per_unit == 0:
        return {"error": "stop equals entry; cannot compute R"}

    for _ in range(sims):
        price = entry
        hit = None
        for _ in range(bars):
            price *= math.exp(rng.choice(returns))
            if side == "long":
                if price <= stop:
                    hit = "stop"
                    break
                if price >= target:
                    hit = "target"
                    break
            else:  # short
                if price >= stop:
                    hit = "stop"
                    break
                if price <= target:
                    hit = "target"
                    break
        if hit == "target":
            n_target += 1
            final_r += abs(target - entry) / r_per_unit
        elif hit == "stop":
            n_stop += 1
            final_r -= 1.0
        else:
            n_neither += 1
            # end-of-session mark-to-market
            r = ((price - entry) if side == "long" else (entry - price)) / r_per_unit
            final_r += r

    return {
        "simulations": sims,
        "p_target_first": round(n_target / sims, 4),
        "p_stop_first": round(n_stop / sims, 4),
        "p_neither": round(n_neither / sims, 4),
        "expected_r": round(final_r / sims, 3),
    }


def historical_backtest(candles, entry_pct_offset: float, stop_pct: float, target_pct: float, session_bars: int) -> dict:
    """Walk historical bars and re-fire the same rule to count wins/losses."""
    fires = wins = losses = neithers = 0
    i = 20
    while i < len(candles) - session_bars - 1:
        # arbitrary fire condition: close > 20-bar high (same spirit as scanner)
        window_high = max(c.h for c in candles[i - 20 : i])
        bar = candles[i]
        if bar.c > window_high:
            fires += 1
            entry = bar.c * (1 + entry_pct_offset)
            stop = entry * (1 - stop_pct)
            target = entry * (1 + target_pct)
            resolved = False
            for j in range(i + 1, i + 1 + session_bars):
                c = candles[j]
                if c.l <= stop:
                    losses += 1
                    resolved = True
                    break
                if c.h >= target:
                    wins += 1
                    resolved = True
                    break
            if not resolved:
                neithers += 1
            i += session_bars  # non-overlapping
        else:
            i += 1
    return {
        "historical_fires": fires,
        "historical_wins": wins,
        "historical_losses": losses,
        "historical_neither": neithers,
        "historical_win_rate": round(wins / fires, 3) if fires else None,
    }


def verdict(result: dict) -> str:
    er = result.get("expected_r", 0)
    if er > 0.25:
        return "edge_positive"
    if er > 0:
        return "edge_marginal"
    return "edge_negative"


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--plan", help="path to plan.json from crypto-exit")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--side", default="long", choices=["long", "short"])
    p.add_argument("--sims", type=int, default=cfg["timemachine"]["simulations"])
    p.add_argument("--bars", type=int, default=cfg["timemachine"]["session_bars"])
    p.add_argument("--seed", type=int)
    args = p.parse_args()

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
        symbol = plan["symbol"]
        entry, stop, target = plan["entry"], plan["stop"], plan["target"]
        side = plan.get("side", "long")
    else:
        if not all([args.symbol, args.entry, args.stop, args.target]):
            print("Need --plan OR all of --symbol --entry --stop --target", file=sys.stderr)
            return 2
        symbol, entry, stop, target, side = args.symbol.upper(), args.entry, args.stop, args.target, args.side

    # Fetch ~60 days of 15m bars (60*24*4 = 5760; capped at 1000 per Binance call → use 1000 bars ~10 days as PoC)
    klines = binance_klines(symbol, interval="15m", limit=1000)
    candles = to_candles(klines)
    closes = [c.c for c in candles]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    mc = simulate(log_returns, entry, stop, target, args.bars, args.sims, side=side, seed=args.seed)

    stop_pct = abs(entry - stop) / entry
    target_pct = abs(target - entry) / entry
    hist = historical_backtest(candles, 0.0, stop_pct, target_pct, args.bars)

    result = {
        "source": "crypto-timemachine",
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "bars": args.bars,
        **mc,
        **hist,
        "warning": "Simulation is NOT a prediction. Day trading loses most people money.",
    }
    result["verdict"] = verdict(result)

    write_signal(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
