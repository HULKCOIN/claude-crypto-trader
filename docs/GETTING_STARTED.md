# Getting Started

A 15-minute walkthrough from clone to first paper-traded signal.

## 1. Install

```bash
git clone https://github.com/HULKCOIN/claude-crypto-trader.git
cd claude-crypto-trader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

## 2. Smoke test one scan

```bash
cd tools
python3 scanner.py --symbol BTCUSDT --json
```

You should see a JSON list (possibly empty — the setup may not be present
right now) and a stderr line like `[scanner] 0 setups across 1 pairs`.

## 3. Scan the whole universe

```bash
python3 scanner.py
```

This fetches the top 50 USDT pairs by 24h volume and runs breakout, flag
and oversold-bounce detection on each. Hits are appended to
`../bridge/signal_queue.jsonl` and printed as JSON.

## 4. Build a trade plan

```bash
# Pick a hit from the scanner. Example: BTCUSDT at 68355 with range high 68300.
python3 exit.py --symbol BTCUSDT --entry 68355.20
```

Writes `../bridge/plans/BTCUSDT_<ts>.json` and prints it.

## 5. Replay 10 000 sessions

```bash
python3 timemachine.py --plan ../bridge/plans/BTCUSDT_<ts>.json
```

Prints `p_target_first`, `p_stop_first`, `expected_r`, historical fire
rate, and a verdict. If `expected_r < 0`, the plan has negative edge on
this pair's recent behavior — skip it.

## 6. Risk gate

```bash
python3 risk.py --plan ../bridge/plans/BTCUSDT_<ts>.json
```

Exits 0 on PASS, 1 on FAIL. Appends the decision (including a plan
fingerprint) to `../bridge/risk_decisions.jsonl`.

## 7. Size the position

```bash
python3 budget.py --plan ../bridge/plans/BTCUSDT_<ts>.json
```

Returns `notional_usd`, `base_qty`, and the dollar risk at stop.

## 8. Place a paper order

```bash
cd ..
python3 tools/broker.py create --plan bridge/plans/BTCUSDT_<ts>.json --size <size.json>
python3 tools/broker.py show
python3 tools/broker.py approve --index 1 --reply yes
python3 tools/broker.py place --index 1
```

With `config.broker.mode == "paper"` (default), the fill is recorded in
`bridge/paper_fills.jsonl`. Nothing hits a real exchange.

## 9. In Claude Code

Install the skills so Claude can orchestrate them conversationally:

```bash
cp -r skills/crypto-* ~/.claude/skills/
```

Then in Claude Code:

```
> /crypto-scanner
> /crypto-exit --symbol BTCUSDT --entry 68355
> /crypto-timemachine
> /crypto-risk
> /crypto-budget
> /crypto-broker
```

Claude will walk you through each step and ask for your approval before
anything mutable happens.

## 10. Agent integration

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture. Minimum:

```python
# my_agent.py
def decide(proposal):
    if proposal["timemachine"]["expected_r"] > 0.3 and proposal["risk"]["verdict"] == "PASS":
        return {"action": "execute", "reason": "edge positive"}
    return {"action": "skip", "reason": "no edge"}
```

```bash
python3 bridge/hermes_bridge.py watch --callback my_agent:decide
```

## 11. 24/7

See [24_7_DEPLOYMENT.md](24_7_DEPLOYMENT.md). Shortest path: enable the
`scan-signals.yml` GitHub Actions workflow — it runs every 15 min
forever on free runners.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ImportError: ccxt` | `pip install -r requirements.txt` |
| Scanner returns nothing | Try `--setups oversold_bounce` or widen `--lookback` |
| 429 from Binance | You're hitting rate limits — slow the cron |
| Alerts never arrive | Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` or `DISCORD_WEBHOOK_URL` |
| `GATE FAIL: no user yes` | Run `broker.py approve --index N --reply yes` first |
| Timemachine says `edge_negative` | **Trust it.** Skip the trade. |
