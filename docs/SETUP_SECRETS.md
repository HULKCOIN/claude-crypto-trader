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
3. Pick a name: `HULKCOIN Crypto Signals`
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

## 2. Binance Testnet — Paper trading on real matching engine (5 min)

**Decide first: Spot or Futures?**

| | Spot | Futures |
|---|---|---|
| URL | `testnet.binance.vision` | `testnet.binancefuture.com` |
| What | Buy & hold tokens, no leverage | USDT-M perpetuals with leverage |
| Start capital | TBTC + TUSDT | TUSDT only |
| Risk | Max loss = position size | Can lose more than deposit |
| Recommended for | First steps | After ≥3 months of spot practice |

### Spot Testnet (recommended start)
1. **https://testnet.binance.vision** → "Log In with GitHub" (fastest)
2. Click your profile → "API Keys"
3. "Generate HMAC_SHA256 Key" → label: `claude-crypto-trader`
4. **Copy BOTH keys immediately** (they're only shown once)
5. GitHub secrets:
   - `BINANCE_SPOT_TESTNET_API_KEY` = API Key
   - `BINANCE_SPOT_TESTNET_API_SECRET` = Secret Key
6. In `config.json`: `"broker": { "market": "spot", "mode": "testnet" }`

### Futures Testnet (later)
1. **https://testnet.binancefuture.com** → register (separate from Spot!)
2. "API" tab → "Create API Key"
3. GitHub secrets:
   - `BINANCE_FUTURES_TESTNET_API_KEY`
   - `BINANCE_FUTURES_TESTNET_API_SECRET`
4. In `config.json`: `"broker": { "market": "futures", "mode": "testnet" }`

### For real trading later (never on GitHub Actions!)
- Keys go **only on your own VPS** as `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- Set `config.broker.mode = "live"` AND `live_confirmed = true`

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
- [ ] `BINANCE_SPOT_TESTNET_API_KEY`
- [ ] `BINANCE_SPOT_TESTNET_API_SECRET`

Optional (adds on-chain whale alerts):
- [ ] `ETHERSCAN_API_KEY`

That's it. 4 required + 1 optional = **5 secrets, all free, zero credit card.**

---

## Verify

1. **https://github.com/HULKCOIN/claude-crypto-trader/actions/workflows/scan-signals.yml**
2. "Run workflow" → "Run"
3. In ~1 min, check `data/snapshots/latest.jsonl` on main
4. If `TELEGRAM_*` secrets are set and the scanner finds a setup, your phone should ping
