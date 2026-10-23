# 24/7 Deployment

Three options, ordered by cost and complexity.

## Option A — GitHub Actions (free, zero infra)

The included `.github/workflows/scan-signals.yml` runs every 15 min on
GitHub's free runners (2 000 minutes/month on free tier, unlimited on
public repos). Each run:

1. Checks out the repo
2. Installs deps
3. Runs scanner, news, sentiment, alerts
4. Snapshots the last 500 signals to `data/snapshots/latest.jsonl`
5. Commits + pushes if changed

**Setup:**

1. Push this repo to GitHub (`git push -u origin main`).
2. Repo Settings → Secrets and variables → Actions → add:
   - `CRYPTOPANIC_TOKEN` (optional, free from cryptopanic.com)
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (optional)
   - `DISCORD_WEBHOOK_URL` (optional)
   - `WHALE_ALERT_TOKEN` (optional, free from whale-alert.io)
3. Actions tab → enable workflows.

**Latency**: cron fires 15 minutes apart. GitHub honors cron with up to
~10 min delay under load. **Not suitable for scalping; fine for swing/
intraday swings.**

**Why no execution here**: GitHub Actions runners are untrusted and
short-lived. Never store exchange API keys in Actions secrets if you
plan to live-trade. Keep execution on a trusted host (Option B/C) and
let Actions only produce signals.

## Option B — Tiny VPS (Hetzner / DigitalOcean / Fly.io, ~5€/month)

For sub-minute reaction and real-time execution.

**Hetzner CX22 (€4.51/mo, Finland or Germany):**

```bash
# On the VPS
git clone https://github.com/HULKCOIN/claude-crypto-trader.git
cd claude-crypto-trader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json

# Secrets in a .env file (ignored by git)
cat > .env <<EOF
CRYPTOPANIC_TOKEN=...
WHALE_ALERT_TOKEN=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
BINANCE_API_KEY=...        # paper-testnet keys to start
BINANCE_API_SECRET=...
EOF

# Systemd units (see examples/systemd/)
sudo cp examples/systemd/*.service /etc/systemd/system/
sudo systemctl enable --now ccrypto-scanner.timer
sudo systemctl enable --now ccrypto-bridge.service
```

The bridge service (`ccrypto-bridge.service`) runs
`python3 bridge/hermes_bridge.py watch --callback bridge.executor:decide`
in a loop and reads the queue in real time.

## Option C — Fly.io machines (free tier covers 1-2 small VMs)

Pros: push-to-deploy, built-in secret storage, free HTTPS, scales to zero.
Cons: wake-from-sleep latency on free tier.

```bash
flyctl launch --name ccrypto --no-deploy
flyctl secrets set CRYPTOPANIC_TOKEN=... BINANCE_API_KEY=...
flyctl deploy
```

Dockerfile template coming in a future commit; a basic `python:3.12-slim`
image with `requirements.txt` + `CMD ["python3", "bridge/hermes_bridge.py", "watch"]`
is enough.

## What NOT to do

- Don't run the live-mode broker on a laptop you close. Keys get stolen,
  orders get stranded, stops don't trigger.
- Don't deploy with `broker.mode: live` on day one. Months of paper first.
- Don't trust GitHub Actions logs to be private — they aren't on public
  repos. Keep this repo private if any secret ever flows through it.
- Don't poll the exchange from more than one place at once — you'll trip
  rate limits and get IP-banned.
