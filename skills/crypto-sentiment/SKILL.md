---
name: crypto-sentiment
description: >-
  Sentiment Index. Pulls the Fear & Greed Index (alternative.me, no key)
  plus optional funding rates, long/short ratios, and open interest from
  Binance Futures. Returns a composite sentiment score to context any
  signal. Use when the user says "market sentiment", "fear and greed",
  "funding rate", "long short ratio", "is market extended".
argument-hint: "[symbol or 'market']"
---

# crypto-sentiment

```bash
python3 tools/sentiment.py                    # overall market
python3 tools/sentiment.py --symbol BTCUSDT   # pair-specific
```

## Output

```json
{
  "ts": "...",
  "fear_greed_index": 72,
  "fear_greed_label": "Greed",
  "funding_rate_8h": 0.0012,
  "long_short_ratio": 1.84,
  "open_interest_change_24h_pct": 8.2,
  "verdict": "market_extended_long",
  "note": "Elevated funding + crowded longs — fade-the-breakout risk higher"
}
```

## Why this matters

A technically perfect breakout at Fear & Greed = 90 and funding = 0.3%/8h
is a different trade than the same setup at F&G = 30. This skill does not
decide — it adds the context for /crypto-timemachine and the user.
