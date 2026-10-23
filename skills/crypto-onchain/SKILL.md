---
name: crypto-onchain
description: >-
  On-chain Watcher. Reads large on-chain transfers (whale alerts) from
  Whale Alert API and from exchange-labeled addresses on Etherscan/BscScan.
  Flags: large deposits to exchanges (potential sell pressure), large
  withdrawals (potential accumulation), stablecoin mints (incoming
  liquidity). The crypto equivalent of trader-pelosi — reads what big
  money is doing. Use when the user says "whale alert", "on-chain",
  "big transfers", "exchange flows", "stablecoin mints".
argument-hint: "[symbol or 'all']"
---

# crypto-onchain

```bash
python3 tools/onchain.py --min-usd 1000000
python3 tools/onchain.py --symbol BTC --flows exchange
```

## Data sources (free tiers)

- **Whale Alert API**: 10 req/min free, needs `WHALE_ALERT_TOKEN`
- **Etherscan**: 5 calls/s free, needs `ETHERSCAN_API_KEY`
- **Blockchain.com**: BTC transactions, no auth

## Output

```json
{
  "ts": "...",
  "source": "crypto-onchain",
  "chain": "ethereum",
  "symbol": "USDT",
  "amount_usd": 50000000,
  "from": "treasury:tether",
  "to": "exchange:binance",
  "signal": "potential_sell_pressure",
  "tx_hash": "0x..."
}
```
