---
name: crypto-risk
description: >-
  Risk Gate. Checks every proposed trade against account-wide limits BEFORE
  it reaches /crypto-broker: max daily loss, max open positions, max
  correlation between positions, max exposure per asset. Returns PASS or
  FAIL with the exact rule that blocked. Use when the user says "check
  risk", "can I take this trade", "am I over my limit", or after
  /crypto-timemachine returns an edge.
argument-hint: "[path to plan.json]"
---

# crypto-risk

Blocks trades that would breach configured limits. No limit can be bypassed
by Claude or any agent.

```bash
python3 tools/risk.py --plan bridge/plans/BTCUSDT_1729658400.json
```

## Checks (all must PASS)

| Check | Default |
|-------|---------|
| Daily realized loss | ≤ 2% of equity |
| Open position count | ≤ 3 |
| New-position correlation with any open position (30d returns) | ≤ 0.75 |
| Single-asset exposure | ≤ 25% of equity |
| Account equity floor | ≥ 50% of initial |

## Output

```json
{
  "verdict": "PASS",
  "fingerprint": "BTCUSDT_long_68355_68032_68894",
  "checks": [
    {"rule": "daily_loss", "status": "pass", "value": -0.3, "limit": -2.0},
    {"rule": "open_positions", "status": "pass", "value": 1, "limit": 3}
  ]
}
```
