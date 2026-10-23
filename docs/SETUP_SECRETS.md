# Setup Secrets — step-by-step (all free, no credit card)

Target page for every secret below:
**https://github.com/HULKCOIN/claude-crypto-trader/settings/secrets/actions**

Click **"New repository secret"** → fill Name + Value → **"Add secret"**.

Total time: ~10 min.

---

## 1. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Phone control (5 min)

### Create the bot
1. Telegram → search **`@BotFather`** → start chat
2. Send: `/newbot`
3. Pick a name: `Crypto Signals`
4. Pick a username ending in `bot`: `hulkcoin_signals_bot`
5. BotFather replies with your token: `123456789:ABC-xyz...`
6. GitHub secret `TELEGRAM_BOT_TOKEN` = that token

### Get your chat ID
1. In Telegram, search your bot by its `@username` and press **Start** (or send "hi")
2. Browser:
   ```
   https://api.telegram.org/bot<FULL_TOKEN>/getUpdates
   ```
   Replace `<FULL_TOKEN>` with the exact token. **No space, no slash** between `bot` and the token.
3. In the JSON reply, find `"chat":{"id":12345678,`
4. GitHub secret `TELEGRAM_CHAT_ID` = that number

---

## 2. Binance Demo Mode — Paper trading inside your real account (5 min)

**Binance Demo Mode** lets you paper-trade from inside your regular binance.com
account. One API key works for BOTH Spot and Futures. The balance is visible
in your normal Binance UI, resettable from there.

Docs: https://developers.binance.com/en/docs/products/spot/demo-mode/general-info

### Create the Demo API Key
1. Log in to **binance.com** with your real account
2. Open **"Binance Demo Trading"** (search in the menu)
3. Go to **"API Key Management"** inside the demo view
4. **"Create API Key"** — pick a label like `claude-crypto-trader`
5. Copy BOTH:
   - **API Key** → `BINANCE_API_KEY`
   - **Secret Key** → `BINANCE_API_SECRET`
   (Secrets are only shown once.)
6. GitHub Secrets page → add BOTH as separate secrets with those exact names.
7. In `config.json`: `"broker": { "market": "spot", "mode": "demo" }`
   (or `"market": "futures"` if you prefer).

The broker will then route to `https://demo-api.binance.com`. Orders show up
in your Binance Demo portfolio exactly like real orders — just with fake money.

### Alternative: old Binance Testnet (NOT needed if you use Demo Mode)
If you ever want the old testnet.binance.vision instead (different account,
different keys), set `mode: "testnet"` and use `_SPOT_TESTNET_*` or
`_FUTURES_TESTNET_*` env vars. Most users should just stick with Demo Mode.

### For real trading later (never on GitHub Actions!)
- SAME env var names (`BINANCE_API_KEY` + `_SECRET`) but from REGULAR API
  (not the Demo Trading page) on your own VPS
- Set `config.broker.mode = "live"` AND `live_confirmed = true`
- Three gates still enforced per order

---

## 3. `ETHERSCAN_API_KEY` — ON-chain whale watcher (optional, 2 min)

**Fully free, instant, no credit card:**
1. **https://etherscan.io/register** → sign up
2. Confirm email → log in
3. Top right profile → "API Keys" → "Add"
4. Label: `claude-crypto-trader` → create
5. GitHub secret `ETHERSCAN_API_KEY` = the key

Without this: BTC whale watcher (blockchain.info, keyless) still works.
With this: ETH + ERC-20 flows into/out of exchange hot wallets.

Free tier: 5 calls/sec, 100 000 calls/day. More than enough.

---

## 4. News — No key needed!

I switched news from CryptoPanic (paid) to free alternatives:
- CoinGecko trending (no key, 30 req/min)
- CoinDesk RSS (unlimited)
- Cointelegraph RSS (unlimited)

Nothing to configure. It just works.

---

## 5. Whale alerts — Alternative to Whale Alert (paid)

Whale Alert dropped their free tier. We replaced it with:
- **Blockchain.info** → BTC whale transfers (keyless, unlimited)
- **Etherscan** → ETH/ERC-20 exchange flows (free key, see #3)

Nothing else to pay for.

---

## Final checklist

Required secrets (minimum for full functionality):
- [ ] `TELEGRAM_BOT_TOKEN`
- [ ] `TELEGRAM_CHAT_ID`
- [ ] `BINANCE_API_KEY` (from Binance Demo Trading → API Key Management)
- [ ] `BINANCE_API_SECRET` (same screen)

Optional (adds on-chain whale alerts for Ethereum):
- [ ] `ETHERSCAN_API_KEY`

That's it. 4 required + 1 optional = **5 secrets, all free, zero credit card.**

---

## Verify

1. **https://github.com/HULKCOIN/claude-crypto-trader/actions/workflows/scan-signals.yml**
2. "Run workflow" → "Run"
3. In ~1 min, check `data/snapshots/latest.jsonl` on main
4. If `TELEGRAM_*` secrets are set and the scanner finds a setup, your phone should ping
