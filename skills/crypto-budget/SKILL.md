---
name: crypto-budget
description: >-
  Position Sizer. Takes the risk-approved plan and computes exact position
  size: notional, base quantity, dollar risk at stop. Supports fixed
  fractional (default), Kelly, and volatility-adjusted sizing. Caps at
  per-asset exposure limits. Use when the user says "how much should I
  buy", "size this position", "position sizing", "how big", or after
  /crypto-risk returns PASS.
argument-hint: "[path to plan.json]"
---

# crypto-budget

```bash
python3 tools/budget.py --plan bridge/plans/BTCUSDT_1729658400.json
```

## Sizing methods

- **fixed_fractional** (default): risk X% of equity per trade (default 0.5%)
- **kelly**: fractional Kelly from /crypto-timemachine odds, capped at 0.25
- **volatility**: scale inversely with ATR to equalize risk across pairs

## Output

```json
{
  "symbol": "BTCUSDT",
  "side": "long",
  "notional_usd": 332.50,
  "base_qty": 0.00486,
  "dollar_risk_at_stop": 5.00,
  "sizing_method": "fixed_fractional",
  "equity_pct": 0.5
}
```
