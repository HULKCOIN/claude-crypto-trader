# Setup Secrets — step-by-step

All four signups below are **free, no credit card, no KYC**. Total time: ~15 minutes.

Target: **https://github.com/HULKCOIN/claude-crypto-trader/settings/secrets/actions**
→ "New repository secret" (one per entry below).

---

## 1. `CRYPTOPANIC_TOKEN` — News Monitor (2 min)

1. Open **https://cryptopanic.com/developers/api/**
2. Click **"Sign up"** (top right)
3. Confirm your email
4. On the API page, your token is displayed as `auth_token=xxxxxxxx...`
5. Copy only the token value (everything after `auth_token=`)
6. In GitHub → New secret:
   - Name: `CRYPTOPANIC_TOKEN`
   - Value: `<the token>`
   - Save

**Free tier**: 500 requests/day — our 15-min cron uses ~96/day. Plenty.

---

## 2. `WHALE_ALERT_TOKEN` — On-chain Watcher (3 min)

1. Open **https://whale-alert.io/signup**
2. Enter email + password
3. Confirm email
4. Log in → go to **"API"** tab
5. Click **"Create API Key"**, pick the **Free** plan
6. Copy the key
7. GitHub → New secret:
   - Name: `WHALE_ALERT_TOKEN`
   - Value: `<the key>`
   - Save

**Free tier**: 10 req/min, 1 hour history, min $500k transfers. For bigger whales + more history ($20/month plan), filter min $1M in `config.json`.

---

## 3. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Push to Phone (5 min)

### Create the bot
1. Open Telegram, search for **`@BotFather`** → start chat
2. Send: `/newbot`
3. Pick a name: e.g. `HULKCOIN Crypto Signals`
4. Pick a username: e.g. `hulkcoin_signals_bot` (must end in `bot`)
5. BotFather sends you a message with the token: `123456789:ABC-xyz...`
6. GitHub → New secret:
   - Name: `TELEGRAM_BOT_TOKEN`
   - Value: `123456789:ABC-xyz...`

### Get your chat ID
1. In Telegram, **search for your new bot** by its username and start it (send any message, e.g. "hi")
2. Open in a browser: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   (replace `<YOUR_TOKEN>` with the full token from step 5)
3. Find `"chat":{"id":123456789,` in the JSON response. That number is your chat ID.
4. GitHub → New secret:
   - Name: `TELEGRAM_CHAT_ID`
   - Value: `123456789`

Test: trigger the workflow manually and if an alert fires, your phone bings.

---

## 4. Binance Testnet — Paper trading on real matching engine (5 min)

Binance Testnet is **the gold standard** for crypto paper trading:
- Real order book, real latency, real matching engine
- Fake money (test USDT, test BTC)
- Same API as live — your code is production-ready on day one

### Get testnet keys
1. Open **https://testnet.binance.vision**
2. Click **"Log In with GitHub"** (top right) — fastest option
3. Once in, click your profile → **"API Keys"**
4. Click **"Generate HMAC_SHA256 Key"**
5. Give it a label like `claude-crypto-trader`
6. Copy BOTH:
   - **API Key** → goes to `BINANCE_TESTNET_API_KEY`
   - **Secret Key** → goes to `BINANCE_TESTNET_API_SECRET`
   (These are only shown ONCE. If you lose them, generate new keys.)
7. GitHub → add BOTH secrets with those exact names.

### Fund your testnet account
- Testnet gives you free TUSDT / TBTC on signup (usually 100 BTC + lots of USDT).
- If you ever "run out", refill at the testnet dashboard.

### Switch broker mode to testnet
`config.json` already defaults to `"mode": "testnet"` after this setup.

### For live trading later (NOT on GitHub Actions, EVER)
- Keys go **only** on your own VPS or laptop as `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- Set `config.broker.mode = "live"` AND `config.broker.live_confirmed = true`
- Three gates still apply (risk pass + user yes + mode = live + confirmed flag)

---

## Verify everything

After adding the secrets, trigger the workflow manually:

**https://github.com/HULKCOIN/claude-crypto-trader/actions/workflows/scan-signals.yml**
→ "Run workflow" → "Run"

In ~1 min:
- News signals appear in `data/snapshots/latest.jsonl`
- Whale signals appear (if any transfers > $1M happened)
- Your Telegram should bing (if a price alert triggered)
- Binance testnet is ready to accept paper orders

---

## What NEVER goes into GitHub secrets

- Real exchange API keys (live mode)
- Private keys of any wallet
- Database/service passwords
- OAuth client secrets

GitHub Actions logs are not encrypted; a compromised workflow could print any
secret. Keep live-trading keys on a trusted host you control.
