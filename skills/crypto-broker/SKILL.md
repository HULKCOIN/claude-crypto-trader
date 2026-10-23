---
name: crypto-broker
description: >-
  Order Executor. Sends approved orders to Binance/Coinbase/Kraken/Bybit
  via CCXT. Paper trading by default. REAL orders require three gates:
  (1) /crypto-risk passed this exact plan today, (2) user typed explicit
  yes for this one order, (3) MODE=live in config. Builds bracket orders
  (entry + stop + take-profit) atomically where the exchange supports it.
  Use when the user says "place the order", "buy it", "execute", "send
  it", "close my position", "flatten", or after /crypto-budget sizes.
argument-hint: "[path to plan.json]"
---

# crypto-broker

**The only skill in this pack that can lead to a real order. Three gates
required, every time, for every order.**

```bash
# List pending tickets
python3 tools/broker.py show

# Approve one (user types yes explicitly)
python3 tools/broker.py approve --index 1 --reply "yes"

# Send approved to exchange (paper by default)
python3 tools/broker.py place --index 1

# Flatten a position
python3 tools/broker.py flatten --symbol BTCUSDT
```

## Supported exchanges (via CCXT)

- Binance (spot + futures)
- Coinbase Advanced
- Kraken
- Bybit
- OKX
- Any exchange CCXT supports (100+)

## Setup

API keys go in environment variables, NEVER in chat or files:

```bash
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."
```

For paper trading, use the exchange's testnet (Binance Testnet, Bybit
Testnet) or the built-in `mode=paper` simulator.

## Three-gate enforcement

1. `tools/broker.py` reads the plan's fingerprint and verifies /crypto-risk
   wrote a `PASS` for that exact fingerprint today.
2. Reads the user's explicit `yes` from the ticket record.
3. Reads `broker.mode` from config.json — only `live` sends real orders.

Any gate missing → order is rejected.

## Flash-crash protection (OCO brackets)

In `demo`/`testnet`/`live` mode, every entry order is immediately followed by
a protective OCO (One-Cancels-the-Other) bracket:

- **Spot (Binance)**: native `/api/v3/orderList/oco` with one STOP_LOSS_LIMIT
  + one LIMIT_MAKER. When the price touches the stop, the take-profit is
  auto-cancelled and vice versa.
- **Futures (binanceusdm)**: STOP_MARKET + TAKE_PROFIT_MARKET as separate
  reduceOnly orders.

This means the stop lives **on Binance's matching engine**, not in our
15-min cron. A flash-crash during our sleep window still triggers the
stop in milliseconds.

If the OCO placement fails (e.g. invalid lot size), the entry is kept
and the broker logs a `WARNING: position is UNHEDGED`. Fix the symbol
filters and try again — do NOT leave an unhedged position overnight.

In `paper` mode no OCO is placed (local simulation only).
