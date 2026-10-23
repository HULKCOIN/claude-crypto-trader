# claude-crypto-trader

**24/7 Crypto Trading Framework — 11 Claude Code Skills + Agent Bridge**

Scan hundreds of pairs, replay every outcome before you risk a dollar, and
let your AI agent of choice (Hermes, GPT, Gemini, or a simple rules engine)
decide what to do with the signals. Analysis-only by default. Three gates
block any real order.

Inspired by [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
ported to **crypto markets** (24/7, no closing bell).

---

## What's inside

```
.
├── skills/           # 11 Claude Code skills (.md instructions)
│   ├── crypto-scanner      — chart scanner across top-N USDT pairs
│   ├── crypto-news         — CryptoPanic breaking news
│   ├── crypto-entry        — bar-by-bar trigger confirmation
│   ├── crypto-exit         — ATR-based stop & target
│   ├── crypto-timemachine  — 10 000-run Monte Carlo
│   ├── crypto-risk         — account-wide risk gate
│   ├── crypto-budget       — position sizing
│   ├── crypto-broker       — CCXT execution (3 gates, paper default)
│   ├── crypto-alerts       — Telegram / Discord push
│   ├── crypto-onchain      — whale transfers (replaces Pelosi)
│   └── crypto-sentiment    — Fear & Greed, funding, L/S
├── tools/            # Python implementations (stdlib + ccxt)
├── bridge/           # JSON signal queue + Hermes/agent bridge
├── .github/workflows/
│   ├── scan-signals.yml    — cron every 15 min (24/7)
│   └── ci.yml              — import smoke tests
└── docs/
    ├── ARCHITECTURE.md
    ├── GETTING_STARTED.md
    └── 24_7_DEPLOYMENT.md
```

## Quick start (local, in Claude Code)

```bash
git clone https://github.com/HULKCOIN/claude-crypto-trader.git
cd claude-crypto-trader
cp config.example.json config.json
pip install -r requirements.txt

# Install the skills so Claude Code sees them
cp -r skills/crypto-* ~/.claude/skills/
```

In Claude Code:

```
/crypto-scanner                   # find setups now
/crypto-exit --symbol BTCUSDT --entry 68355
/crypto-timemachine --symbol BTCUSDT --entry 68355 --stop 68032 --target 68894
/crypto-risk --plan bridge/plans/BTCUSDT_<ts>.json
/crypto-budget --plan bridge/plans/BTCUSDT_<ts>.json
/crypto-broker create --plan ... --size ...
```

Every tool is also a plain Python script — run them from any shell, cron,
Modal, Fly.io, GitHub Actions, or your own agent.

## 24/7 without your own server

The included `.github/workflows/scan-signals.yml` runs the scanner, news,
sentiment and alerts every 15 minutes, forever, on GitHub's free runners.
Snapshots the last 500 signals back into the repo so your agent can pull
them from anywhere. See [docs/24_7_DEPLOYMENT.md](docs/24_7_DEPLOYMENT.md).

## Setup secrets (15 min, all free)

See **[docs/SETUP_SECRETS.md](docs/SETUP_SECRETS.md)** for click-by-click
instructions for CryptoPanic, Whale Alert, Telegram, and Binance Testnet.

## Agent integration (Hermes, GPT, Gemini, …)

Every skill writes its output as one JSON line to `bridge/signal_queue.jsonl`.
Any agent can poll that queue. The included `bridge/hermes_bridge.py`
assembles complete trade proposals (scanner + exit + timemachine + risk +
budget) and hands each to your callback:

```python
# my_agent.py
def decide(proposal: dict) -> dict:
    if proposal["timemachine"]["expected_r"] > 0.3 and proposal["risk"]["verdict"] == "PASS":
        return {"action": "execute", "reason": "edge + risk ok"}
    return {"action": "skip", "reason": "no edge"}
```

```bash
python3 bridge/hermes_bridge.py watch --callback my_agent:decide
```

A reference auto-executor is in `bridge/executor.py` (paper-only by safety).

## The three gates (real-order safety)

`tools/broker.py` enforces, per order:

1. `/crypto-risk` wrote `PASS` for the exact plan fingerprint today
2. User typed explicit `yes` for this ticket
3. `config.broker.mode == "live"` (default: `paper`)

Any gate missing → order rejected. Even a rogue agent cannot bypass this
from inside the skill pack. If you build your own executor via
`bridge/executor.py`, YOU are responsible for the gates.

## License

MIT. See [LICENSE](LICENSE).

## ⚠️ Disclaimer

This is open-source software for research and paper trading. It does not
constitute financial advice. **Day trading loses most people money.**
Use at your own risk. The authors accept no liability for any losses.

Never commit API keys. Never run with real money until you have months of
proven forward-tested paper results. Keep risk per trade below 1 %.
