---
name: crypto-alerts
description: >-
  Alerts. Pushes notifications to Telegram, Discord, or stdout when a
  configured price, stop, or target is hit, or when a high-quality setup
  appears. Use when the user says "alert me when", "notify me", "send a
  push", "ping me on", "watch this level".
argument-hint: "[symbol] [price_level]"
---

# crypto-alerts

```bash
# Set a price alert
python3 tools/alerts.py add --symbol BTCUSDT --above 70000

# List active alerts
python3 tools/alerts.py list

# Run the watcher loop (triggered by GitHub Actions cron)
python3 tools/alerts.py watch
```

## Channels

Configure via env vars:
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Telegram push
- `DISCORD_WEBHOOK_URL` — Discord channel webhook
- Default fallback: stdout + append to `logs/alerts.log`
