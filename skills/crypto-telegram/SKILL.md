---
name: crypto-telegram
description: >-
  Telegram Command Bot. Lets the user control the whole trading system from
  their phone with short slash-commands: /scan /signals /positions /open
  /close /flat /pause /resume /status /sentiment. Runs as a long-polling
  daemon. Only chat IDs listed in TELEGRAM_CHAT_ID can issue commands.
  Use when the user says "control from phone", "telegram commands",
  "pause the bot", "close everything", or wants a mobile interface.
argument-hint: ""
---

# crypto-telegram

Mobile control for the trading system. The bot runs 24/7 and listens for
your commands. Nothing happens from Telegram that could not also happen
from the CLI — this is just a convenience front-end.

```bash
# Start the bot (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in env)
python3 tools/telegram_bot.py
```

## Commands

| Command | What it does |
|---------|--------------|
| `/start`, `/help` | Show the command list |
| `/scan` | Run `scanner.py --top-n 20`, reply with top 5 setups |
| `/signals` | Last 20 entries from `bridge/signal_queue.jsonl` |
| `/positions` | Open positions (spot holdings or futures positions, auto-detects) |
| `/sentiment` | Fear & Greed + funding rate + L/S ratio |
| `/open SYM` | Full pipeline: exit → risk → budget → broker (uses current price as entry) |
| `/close SYM` | Market-close an open position |
| `/flat` | Market-close ALL open positions (futures) |
| `/pause` | Set the auto-trader pause flag — new signals are ignored |
| `/resume` | Clear the pause flag |
| `/status` | Current mode (spot/futures, paper/testnet/live) + account equity |

## Security

- Only chat IDs in `TELEGRAM_CHAT_ID` env var may issue commands.
- Multi-user: comma-separate IDs: `TELEGRAM_CHAT_ID="12345,67890"`.
- Unauthorized messages are silently ignored (no error reply — avoids spam).
- The bot never echoes keys or secrets.

## Running 24/7

As a systemd user service on your VPS:

```ini
# /etc/systemd/system/ccrypto-telegram.service
[Unit]
Description=Claude Crypto Trader — Telegram command bot
After=network-online.target

[Service]
WorkingDirectory=/opt/claude-crypto-trader
EnvironmentFile=/opt/claude-crypto-trader/.env
ExecStart=/opt/claude-crypto-trader/.venv/bin/python3 tools/telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ccrypto-telegram
sudo journalctl -u ccrypto-telegram -f
```
