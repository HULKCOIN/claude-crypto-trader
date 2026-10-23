---
name: crypto-news
description: >-
  News & Social Monitor. Pulls breaking crypto news from CryptoPanic (free
  tier) and matches it to the active watchlist. Flags high-impact items
  (exchange listings, hacks, regulatory, large funding rounds, protocol
  upgrades) and attaches them to any pending signal. Use when the user
  says "any news on X", "news monitor", "scan news", "what's the catalyst",
  "why is X pumping", "any breaking news".
argument-hint: "[symbol or 'all']"
---

# crypto-news

Watches CryptoPanic for breaking news on the universe and attaches context
to signals.

```bash
# Pull news for all watched pairs
python3 tools/news.py

# One symbol
python3 tools/news.py --symbol BTC
```

## Config

Set `CRYPTOPANIC_TOKEN` in the environment (free tier: 500 req/day, enough
for a 5-min cron). Without a token the tool falls back to the public RSS,
which has no filtering and higher latency.

## Output

Appends enriched entries to `bridge/signal_queue.jsonl`:

```json
{
  "ts": "...",
  "source": "crypto-news",
  "symbol": "SOL",
  "headline": "Solana ETF application accepted by SEC",
  "severity": "high",
  "url": "https://...",
  "sentiment": "positive"
}
```
