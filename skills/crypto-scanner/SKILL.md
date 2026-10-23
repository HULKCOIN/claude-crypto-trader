---
name: crypto-scanner
description: >-
  Chart Scanner. Scans the top N USDT pairs on Binance (default 50) for
  tradable setups: breakouts above opening range or prior swing high, bull
  flags after an impulse, and oversold bounces off a swing low. Pulls 15m
  klines from Binance public REST (no key needed), ranks candidates by
  setup quality (volume surge, ATR, R:R), and writes the result as JSON to
  stdout and to bridge/signal_queue.jsonl. Use when the user says
  "scan the market", "find setups", "what's moving", "crypto scanner",
  "any breakouts", "scan BTC/ETH/altcoins".
argument-hint: "[symbol or 'all'] [timeframe like 15m, 1h, 4h]"
---

# crypto-scanner

Scans the configured universe for breakouts, bull flags and oversold bounces.
Writes ranked candidates as structured JSON that /crypto-entry, /crypto-exit
and external agents can consume.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Scripts live in `tools/` at the
repo root.

```bash
# Scan default universe (top 50 USDT pairs on Binance, 15m)
python3 tools/scanner.py

# Scan one pair
python3 tools/scanner.py --symbol BTCUSDT --timeframe 1h

# Change setup types
python3 tools/scanner.py --setups breakout,flag

# JSON only (for piping to an agent)
python3 tools/scanner.py --json
```

## Output shape

Each candidate is a JSON object appended to
`bridge/signal_queue.jsonl` with:

```json
{
  "ts": "2026-10-23T06:30:00Z",
  "source": "crypto-scanner",
  "symbol": "BTCUSDT",
  "exchange": "binance",
  "timeframe": "15m",
  "setup": "breakout",
  "price": 68421.50,
  "range_high": 68300.00,
  "atr": 215.4,
  "volume_zscore": 2.8,
  "quality": 0.82,
  "notes": "Breakout above 24-bar opening range on 2.8σ volume"
}
```

## What this skill does NOT do

- Does not place orders (that is /crypto-broker).
- Does not decide position size (that is /crypto-budget).
- Does not call Claude. Pure Python + Binance REST.

## Rate limits & cost

Binance public endpoints: 1200 request-weight per minute, no auth. Scanning
50 pairs costs ~50 weight. Running every 5 min stays well under limits.
