---
name: crypto-exit
description: >-
  Exit Planner. Sets stop-loss and take-profit BEFORE entry, based on
  ATR, recent swing structure, and the setup's own invalidation level.
  Default: stop = entry - 1.5 × ATR(14), target = entry + 2.5 × ATR(14)
  (configurable R:R). Writes a complete plan.json that /crypto-timemachine
  will simulate. Use when the user says "set the stop", "where's my
  target", "plan the exit", "R:R", or after /crypto-entry fires.
argument-hint: "[symbol] [entry_price]"
---

# crypto-exit

```bash
python3 tools/exit.py --symbol BTCUSDT --entry 68355.20
```

## Output: plan.json

```json
{
  "symbol": "BTCUSDT",
  "side": "long",
  "entry": 68355.20,
  "stop": 68032.10,
  "target": 68893.80,
  "atr_14": 215.4,
  "r_per_unit": 323.10,
  "reward_risk": 1.67,
  "invalidation_reason": "below range low AND prior swing low"
}
```
