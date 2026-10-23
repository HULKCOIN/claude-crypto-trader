---
name: crypto-entry
description: >-
  Entry Timing. Takes a candidate from /crypto-scanner and watches it bar
  by bar for the actual trigger: close above range high, retest hold,
  volume confirmation. Returns the entry price, the exact bar it fired,
  and a go/no-go verdict. Use when the user says "when do I enter",
  "entry timing", "wait for trigger", "did the setup fire", or after
  /crypto-scanner surfaces a candidate.
argument-hint: "[symbol] [range_high]"
---

# crypto-entry

Monitors a scanner candidate for the actual entry trigger. Rules:

1. **Breakout**: close > range_high AND volume > 1.5× average
2. **Flag**: close back above flag high after pullback AND lower-low broken
3. **Oversold bounce**: RSI(14) crosses up through 30 AND bullish candle

```bash
python3 tools/entry.py --symbol BTCUSDT --setup breakout --range-high 68300
```

## Output

```json
{
  "ts": "...",
  "source": "crypto-entry",
  "symbol": "BTCUSDT",
  "verdict": "go",
  "entry_price": 68355.20,
  "trigger_bar": "2026-10-23T06:30:00Z",
  "confirmation": {
    "close_above_range": true,
    "volume_confirmed": true,
    "no_wick_rejection": true
  }
}
```

Hands off to `/crypto-exit` (which sets stop + target) and then
`/crypto-timemachine` (which simulates the plan).
