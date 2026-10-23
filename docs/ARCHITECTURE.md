# Architecture

## Why this design

Jake's day-trader-skill proved that an "ensemble of specialized skills" is
more honest than a monolithic trading bot. Each skill has one job, logs
its reasoning, and refuses to overreach. This repo ports that pattern to
24/7 crypto markets and adds an **agent bridge** so any AI agent can
subscribe to signals and drive execution without touching the Claude
code path.

## Data flow

```
                   ┌─────────────────────────────────────────┐
                   │          Universe (Binance USDT)         │
                   │   top-N pairs by 24h quote volume        │
                   └──────────────────┬──────────────────────┘
                                      ▼
          ┌────────────────────────────────────────────────────┐
          │                 ANALYSIS LAYER                     │
          │                                                    │
          │  /crypto-scanner → candidates (breakout/flag/...)  │
          │  /crypto-news    → catalyst context                │
          │  /crypto-onchain → whale flows                     │
          │  /crypto-sentiment → F&G, funding, L/S             │
          │                                                    │
          │        ↓ for each candidate:                       │
          │  /crypto-entry      → trigger confirmation         │
          │  /crypto-exit       → stop + target → plan.json    │
          │  /crypto-timemachine → 10 000 MC + historical      │
          │                                                    │
          │  Every output → one JSON line in                   │
          │  bridge/signal_queue.jsonl                         │
          └──────────────────┬────────────────────────────────┘
                             ▼
          ┌────────────────────────────────────────────────────┐
          │             DECISION LAYER (your agent)            │
          │                                                    │
          │  bridge/hermes_bridge.py assembles proposals from  │
          │  the queue (scanner + plan + MC + risk + budget).  │
          │                                                    │
          │  Your callback decides: execute / skip             │
          │  Choices for callback:                             │
          │   - bridge/executor.py (rules-based reference)     │
          │   - a Hermes / GPT / Gemini agent                  │
          │   - your own Python function                       │
          └──────────────────┬────────────────────────────────┘
                             ▼
          ┌────────────────────────────────────────────────────┐
          │                EXECUTION LAYER                     │
          │                                                    │
          │  Path A: /crypto-broker (3-gate human approval)    │
          │  Path B: bridge/executor.py (paper, auto)          │
          │  Path C: direct CCXT (your custom code)            │
          │                                                    │
          │  All → CCXT → Binance / Coinbase / Kraken / Bybit  │
          └────────────────────────────────────────────────────┘
```

## Where things live

| Layer | Reads | Writes |
|-------|-------|--------|
| Analysis | Binance REST, CryptoPanic, Whale Alert, alternative.me | `bridge/signal_queue.jsonl`, `bridge/plans/*.json`, `bridge/risk_decisions.jsonl` |
| Decision | `signal_queue.jsonl` | stdout, custom log |
| Execution | plans + size + risk log | `bridge/tickets.jsonl`, `bridge/paper_fills.jsonl`, exchange API |

## Why the queue is a plain JSONL file

- Zero infrastructure: works on a laptop, a Pi, GitHub Actions, any VPS.
- Append-only: no concurrency bugs.
- Readable by any language: Python, Go, Rust, bash + jq.
- Easy to replay for backtesting.

If you outgrow it, swap `bridge/signal_queue.py` for Redis Streams or
Kafka without changing any skill.

## Why no database

By design. State lives in:

- JSONL for events (queue, risk log, tickets, fills)
- One JSON file for positions (`bridge/positions.json`)
- The exchange's own books for truth

If you need queries, pipe the JSONL into DuckDB or SQLite on demand.

## Why the three gates (and why you shouldn't disable them)

`/crypto-broker` cannot send a live order unless:

1. **A /crypto-risk PASS exists today for this exact plan.** The fingerprint
   hashes symbol+side+entry+stop+target, so even changing the stop by one
   tick invalidates it.
2. **The user typed `yes` for this specific ticket.** Not "yes to anything",
   not "yes to this symbol". *This* ticket.
3. **`config.broker.mode == "live"`.** The default is `paper`. You have to
   change the config and commit it, which is auditable.

Each gate exists because a specific class of bug or AI mistake was
imagined. Removing any one of them re-opens that class.

If you want full automation, use `bridge/executor.py` — it is explicitly
labeled "paper only by safety" and is where YOU own the risk. The skills
themselves stay honest.

## 24/7 scheduling model

GitHub Actions `*/15 * * * *` is the simplest free way. For sub-minute
latency, run `python3 bridge/hermes_bridge.py watch` on a tiny VPS
(Hetzner CX11, 4€/month) and have *it* trigger the analysis tools on
demand instead of waiting for cron.

Crypto latency reality check: Claude round-trip ≈ 2–8 s. If your edge
depends on sub-second execution, you need a non-LLM path. These skills
are not that path.
