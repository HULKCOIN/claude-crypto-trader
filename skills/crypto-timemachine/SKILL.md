---
name: crypto-timemachine
description: >-
  Time Machine. Replays the rest of the session 10,000 times before you
  risk a dollar. Takes a plan.json from /crypto-exit, bootstraps the next
  N bars from the pair's own recent 60 days of 15m data, and re-runs the
  same stop/target rule on every past day. Returns the honest odds: P(target
  first), P(stop first), P(neither), expected R, and the full outcome
  distribution. Hands off to /crypto-budget. Use when the user says "run
  the time machine", "simulate this trade", "monte carlo", "what are the
  odds", "backtest this setup", "is this a coin flip".
argument-hint: "[path to plan.json | symbol]"
---

# crypto-timemachine

The Time Machine is the honest reality check. It does NOT predict the future.
It asks: "If today plays out like the pair's own last 60 days, how often
does this exact plan hit the target first vs the stop first?"

```bash
# From a plan file written by /crypto-exit
python3 tools/timemachine.py --plan bridge/plans/BTCUSDT_1729658400.json

# Ad-hoc from CLI args
python3 tools/timemachine.py --symbol BTCUSDT --entry 68355 --stop 68032 --target 68894
```

## Method

1. Fetch last 60 days of 15m klines from Binance.
2. Build an empirical distribution of per-bar log returns.
3. Bootstrap 10,000 forward paths of `session_bars` length (default 24 bars = 6h).
4. For each path, walk bar-by-bar: did it hit stop first, target first, or neither?
5. Also re-run the same rule on every historical session that would have fired this setup.

## Output

```json
{
  "symbol": "BTCUSDT",
  "simulations": 10000,
  "p_target_first": 0.52,
  "p_stop_first": 0.37,
  "p_neither": 0.11,
  "expected_r": 0.48,
  "historical_fires": 23,
  "historical_win_rate": 0.57,
  "verdict": "edge_positive",
  "warning": "Simulation is NOT a prediction. Day trading loses most people money."
}
```

## What it is NOT

- Not a profit guarantee.
- Not a predictor.
- Not a replacement for forward-tested paper trading.
