# GCP e2-micro Always Free — Schritt-für-Schritt Deployment

**Ergebnis am Ende:** Deine Bots laufen 24/7, Commands reagieren in **unter 1 Sekunde**, kosten **0 €/Monat forever**.

**Dauer:** ca. 15-20 Minuten

**Was du brauchst:**
- Google-Account (hast du schon, wegen Gemini)
- Kreditkarte für GCP-Verifizierung (nur Verifizierung, wird nix abgebucht wenn du in Free Tier bleibst)

**WICHTIG:** Always-Free gibt's bei GCP **nur in US-Regionen** (us-west1, us-central1, us-east1). Frankfurt wäre kostenpflichtig. Latenz US→Telegram ist ~150ms, merkt man null.

---

## Teil 1 — GCP-Projekt anlegen (3 Min)

1. **https://console.cloud.google.com/** öffnen → mit deinem Google-Account einloggen (gleicher wie bei Gemini)
2. Falls neu bei GCP: AGB akzeptieren, Land Germany, Kreditkarte verifizieren → du bekommst 300$ Trial, aber Always-Free läuft **unabhängig davon forever weiter**
3. Oben in der Leiste **Projekt-Dropdown** → **"New Project"** → Name: `ccrypto-trader` → Create

---

## Teil 2 — VM in Cloud Shell erstellen (5 Min)

Du machst alles direkt im Browser. Keine CLI-Installation nötig.

1. Oben rechts in der Google Cloud Console auf das **Terminal-Icon** klicken (`>_`) → **"Activate Cloud Shell"**
2. Unten im Browser öffnet sich ein Terminal (dauert ~30 Sek beim ersten Mal)
3. Diese Befehle **einen nach dem anderen** reinkopieren:

```bash
# Projekt setzen (ersetze PROJECT_ID falls du es anders genannt hast)
gcloud config set project ccrypto-trader

# Compute Engine API aktivieren
gcloud services enable compute.googleapis.com

# VM erstellen (us-west1-b — Always Free berechtigt)
gcloud compute instances create ccrypto-trader \
  --zone=us-west1-b \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=ccrypto-trader
```

Nach ~1 Min siehst du eine Zeile mit der externen IP → **merken** (steht in der Spalte `EXTERNAL_IP`).

---

## Teil 3 — Auf die VM einloggen (1 Min)

Direkt aus Cloud Shell (kein SSH-Key-Theater nötig, GCP macht das automatisch):

```bash
gcloud compute ssh ccrypto-trader --zone=us-west1-b
```

Beim ersten Mal fragt es: *"Do you want to generate a key pair?"* → **Y** drücken. Danach bist du drin, siehst `username@ccrypto-trader:~$`.

---

## Teil 4 — Bootstrap-Script ausführen (3 Min)

In der VM:

```bash
wget https://raw.githubusercontent.com/HULKCOIN/claude-crypto-trader/main/deploy/oracle_bootstrap.sh
chmod +x oracle_bootstrap.sh
sudo ./oracle_bootstrap.sh
```

> Ja, das Script heißt `oracle_bootstrap.sh` — funktioniert aber 1:1 für Ubuntu auf GCP. Nur der Dateiname ist historisch.

Läuft ~2-3 Min durch. Am Ende steht **"Bootstrap FERTIG"**.

---

## Teil 5 — Secrets eintragen (3 Min)

```bash
sudo nano /opt/ccrypto-trader/.env
```

Jede Zeile mit `TODO_` ersetzen durch deinen echten Wert. Die hast du alle schon in GitHub Secrets.

**Beispiel wie es aussieht:**
```
TELEGRAM_BOT_TOKEN=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw
TELEGRAM_CHAT_ID=8398295986
TELEGRAM_OWNER_CHAT_ID=8398295986
TELEGRAM_NEWS_BOT_TOKEN=8953711449:AAFxyz…
TELEGRAM_NEWS_CHAT_ID=-1004382218113
BINANCE_API_KEY=xxxxxxxxxxxxxxx
BINANCE_API_SECRET=xxxxxxxxxxxxx
ETHERSCAN_API_KEY=xxxxxx
GEMINI_API_KEY=AIzaxxxxxxxx
MIN_EDGE_R=0.0
STOP_ATR_MULT=2.5
TARGET_ATR_MULT=4.5
BROKER_MODE_HINT=demo
```

Speichern: **Ctrl+O** → Enter → **Ctrl+X**

---

## Teil 6 — Services starten (1 Min)

```bash
sudo systemctl enable --now ccrypto-scanner.timer
sudo systemctl enable --now ccrypto-telegram.service
sudo systemctl enable --now ccrypto-newsbot.service
```

Checken:
```bash
sudo systemctl status ccrypto-telegram --no-pager
sudo systemctl status ccrypto-newsbot --no-pager
```

Beide sollten **"active (running)"** zeigen.

---

## Teil 7 — Testen

Trading-Bot auf Telegram:
```
/status
```
→ antwortet in **unter 1 Sekunde**

News-Gruppe:
```
/news
```
→ instant.

---

## Logs live mitlesen

```bash
sudo tail -f /opt/ccrypto-trader/logs/telegram.log
sudo tail -f /opt/ccrypto-trader/logs/newsbot.log
```

(Ctrl+C beendet)

---

## Updates einspielen (wenn ich Code pushe)

```bash
cd /opt/ccrypto-trader
sudo git pull
sudo systemctl restart ccrypto-telegram ccrypto-newsbot
```

---

## Troubleshooting

**"Bot antwortet nicht"**
```bash
sudo journalctl -u ccrypto-telegram -n 50 --no-pager
```
→ sagt was fehlt, meist eine leere .env-Variable

**"Secrets geändert"**
```bash
sudo nano /opt/ccrypto-trader/.env
sudo systemctl restart ccrypto-telegram ccrypto-newsbot
```

**"VM mal neu starten"**
Via Cloud Shell:
```bash
gcloud compute instances reset ccrypto-trader --zone=us-west1-b
```

**"Alles löschen und neu"**
```bash
gcloud compute instances delete ccrypto-trader --zone=us-west1-b
```

---

## WICHTIG — Kosten im Blick

Always-Free bedingt diese Grenzen (die du mit 1 e2-micro locker einhältst):
- **1 e2-micro** in us-west1/us-central1/us-east1
- **30 GB Standard-Persistent-Disk**
- **1 GB egress/Monat** US→außerhalb (reicht locker für Bot-Traffic)

**Rechnung öffnen:** https://console.cloud.google.com/billing — siehst du jederzeit was du zahlen würdest.

**Wenn du dir unsicher bist** → setze ein **Budget-Alert auf 1€** (Billing → Budgets & Alerts → Create Budget). Bekommst Email bevor je was abgerechnet wird.

---

## WICHTIG — Sicherheit

- `.env` liegt NUR auf deiner VM, nie im Git-Repo, nie bei mir.
- SSH ist nur via gcloud möglich (dein Google-Login).
- Keine Ports offen außer SSH — Bots sind outbound-only.
- **Niemals** API-Keys im Chat/E-Mail posten.
