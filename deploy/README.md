# GCP Deployment Guide (deutsch)

Komplette Anleitung um den HULKCOIN Crypto Trader auf einer eigenen
Google Cloud VM in Frankfurt zu betreiben. **24/7 echter Binance-Zugang**
(nicht geoblockt wie GitHub Actions US), **interaktiver Telegram-Bot** mit
`/scan` `/open` `/close` Befehlen, **alle 5 Min** statt 15.

---

## Was dein Setup bekommt

| Feature | GitHub Actions (jetzt) | GCP VM (nach Deploy) |
|---------|----------------------|---------------------|
| Scanner-Takt | alle 15 Min | **alle 5 Min** |
| Binance-Zugang | ❌ 403 geoblockt | ✅ volle Pair-Auswahl |
| Interactive `/scan` `/open` | ❌ geht nicht (nur Cron) | ✅ Long-Polling-Daemon |
| Echte OCO-Orders mit Flash-Crash-Schutz | ❌ nur Paper | ✅ echte Demo-Orders |
| Latenz Signal → Order | ~15 Min | ~Sekunden |
| Kosten | 0 € | 0 € (us-west1) oder 6 €/Monat (Frankfurt) |

---

## Schritt 1: gcloud CLI installieren

Auf deinem **lokalen PC** (Windows / Mac / Linux):

- Windows: https://cloud.google.com/sdk/docs/install
- Mac: `brew install --cask google-cloud-sdk`
- Linux: Siehe Google-Cloud-Doku

Dann einloggen:
```bash
gcloud auth login
gcloud config set project PROJECT_ID
```

(Deine PROJECT_ID findest du oben in der Google Cloud App — bei dir heißt sie z.B. `project-6b4f76d0-1960-4d95-ba7`.)

---

## Schritt 2: Repo lokal klonen und Setup starten

```bash
git clone https://github.com/HULKCOIN/claude-crypto-trader.git
cd claude-crypto-trader
bash deploy/gcp_setup.sh
```

Das Script:
1. Fragt dich zur Bestätigung
2. Aktiviert die Compute Engine API
3. Erstellt eine `e2-micro` VM in `europe-west3-c` (Frankfurt — **Binance geht**)
4. Lässt dort automatisch `vm_bootstrap.sh` laufen (installiert Python, klont Repo, legt systemd Services an)
5. Zeigt dir am Ende SSH-Befehl + externe IP

**Dauer**: ~3-5 Minuten.

---

## Schritt 3: Secrets auf die VM

```bash
gcloud compute ssh ccrypto-trader --zone=europe-west3-c
```

Du bist jetzt auf der VM. Lege die `.env` an:
```bash
cd /opt/ccrypto-trader
sudo nano .env
```

Inhalt (die Werte aus deinen GitHub Secrets kopieren):
```
TELEGRAM_BOT_TOKEN=7891234567:AAHxyz...
TELEGRAM_CHAT_ID=8398295986
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
ETHERSCAN_API_KEY=...
GEMINI_API_KEY=...          # Optional, für Gemini-LLM-Decider
```

Speichern: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Schritt 4: Dienste starten

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ccrypto-scanner.timer
sudo systemctl enable --now ccrypto-telegram.service
```

Fertig! Ab jetzt:
- Alle **5 Min** läuft Scanner + News + Whales + Push + Auto-Trader
- Dein Handy bekommt Push-Alerts
- `@claude_trading_signale_bot` reagiert auf `/scan` `/open BTCUSDT` `/close` `/flat` `/status`

---

## Logs anschauen

```bash
sudo journalctl -u ccrypto-telegram -f       # Telegram-Bot live
sudo journalctl -u ccrypto-scanner -f        # Scanner-Läufe live
tail -f /opt/ccrypto-trader/logs/scanner.log
tail -f /opt/ccrypto-trader/logs/telegram.log
```

---

## Kosten im Blick behalten

Du hast `jonasvolk1703@gmail.com` mit `~$170` Credit.

**Billing-Alert einrichten** (empfohlen):
```bash
gcloud billing budgets create \
    --billing-account=XXXXXX-XXXXXX-XXXXXX \
    --display-name=ccrypto-budget \
    --budget-amount=20USD
```

Oder in der GCP Konsole: *Billing → Budgets & alerts → Create budget*.

**e2-micro in europe-west3**: ~6 €/Monat. Mit $170 Credit → ~28 Monate.
**e2-micro in us-west1/central1/east1**: 0 € always-free (aber Binance könnte dort noch gehen — testen!).

---

## Updates einspielen

Jedes Mal wenn ich hier was ändere und pushe:
```bash
# Auf der VM
cd /opt/ccrypto-trader
sudo git pull
sudo .venv/bin/pip install -r requirements.txt    # falls neue Dependencies
sudo systemctl restart ccrypto-telegram
```

---

## VM pausieren (Kosten sparen wenn du mal nicht tradest)

```bash
# Stoppen (keine Compute-Kosten, nur Disk):
gcloud compute instances stop ccrypto-trader --zone=europe-west3-c

# Wieder starten:
gcloud compute instances start ccrypto-trader --zone=europe-west3-c
```

---

## VM komplett löschen (alles weg)

```bash
gcloud compute instances delete ccrypto-trader --zone=europe-west3-c
```

Secrets in GitHub bleiben erhalten — die VM ist nur Hosting.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gcloud: command not found` | gcloud CLI installieren (siehe Schritt 1) |
| `Permission denied (publickey)` beim SSH | `gcloud compute config-ssh` einmal ausführen |
| Bot antwortet nicht auf `/scan` | `sudo systemctl status ccrypto-telegram` prüfen |
| Keine Signale mehr | `sudo journalctl -u ccrypto-scanner --since '10 min ago'` |
| Binance 451 auf VM | Falls VM NICHT in Europa → VM löschen und mit `ZONE=europe-west3-c` neu anlegen |

---

## Nächste Erweiterungen (bereits im Code vorbereitet)

- **OCO-Orders** (Flash-Crash-Schutz): `broker.py mode: demo` statt `paper` + echte Stop-Loss-Orders direkt auf Exchange
- **Gemini LLM-Decider**: `GEMINI_API_KEY` setzen → jeder Trade bekommt zusätzliche LLM-Review
