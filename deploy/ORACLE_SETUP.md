# Oracle Cloud Always Free — Schritt-für-Schritt Deployment

**Ergebnis am Ende:** Deine Bots laufen 24/7, reagieren auf Commands in **unter 1 Sekunde**, kosten **0 €/Monat forever**.

**Dauer:** ca. 15-20 Minuten

**Was du brauchst:**
- Email-Adresse
- Handy (für SMS-Verifizierung)
- Kreditkarte (nur zur Account-Verifizierung — es wird NICHTS abgebucht, Oracle Always Free bleibt forever free)

**WICHTIG — Sicherheit:** Alle deine API-Keys/Tokens tippst du direkt in deinen Browser oder in die VM. **Niemals** in Chats, E-Mails oder Cloud-Shares. Das ist dein eigenes Zeug.

---

## Teil 1 — Oracle Cloud Account anlegen (5 Min)

1. Gehe auf **https://signup.cloud.oracle.com/**
2. **Land: Germany**, Account-Type: **Individual**
3. Email + Passwort + Handy-Nummer → verifizieren
4. **Home Region: Germany Central (Frankfurt)** oder **Germany Central (Hamburg)** — ganz wichtig, kann nachher nicht geändert werden
5. Kreditkarte eintragen (wird nur verifiziert, Always Free Resources sind kostenlos)
6. Fertig — du landest im **OCI Dashboard**

> **Falls bei Kreditkarte nicht geht:** Oracle verlangt in Deutschland normalerweise eine. Falls du keine hast, nimm statt Oracle das **GCP e2-micro** (US-Region, keine Karte nötig) — sag mir Bescheid, dann adaptiere ich das.

---

## Teil 2 — Compute-Instance (VM) erstellen (5 Min)

1. Im OCI-Dashboard oben links auf **"≡ Menu" → "Compute" → "Instances"**
2. **"Create Instance"** klicken
3. **Name:** `ccrypto-trader` (egal, Name ist frei)
4. **Image & Shape** → **"Edit"** klicken:
   - **Image:** "Canonical Ubuntu 24.04" wählen
   - **Shape:** "Change Shape" → **"Ampere" Tab** → **"VM.Standard.A1.Flex"**
     - **OCPUs: 2** (nach rechts schieben)
     - **Memory: 12 GB** (nach rechts schieben)
     - *(Das ist noch im Always-Free-Limit)*
5. **Networking:** lass alles auf Default (automatisches VCN + Public IP)
6. **SSH-Keys:** wähle **"Generate a key pair for me"** → **"Save Private Key"** klicken → Datei `ssh-key-xxx.key` wird heruntergeladen
   - **Private Key speichern! Ohne den kommst du nie wieder auf die VM!**
7. **"Create"** unten klicken → warte ~1 Min bis Status **"Running"** ist
8. Notiere dir die **"Public IP Address"** aus der Instance-Übersicht (z.B. `130.61.xxx.xxx`)

---

## Teil 3 — Auf die VM einloggen (2 Min)

**Option A — Browser-basiert (einfachste, nichts installieren):**

1. In OCI-Dashboard: Compute → Instances → `ccrypto-trader` öffnen
2. **"Cloud Shell Connection"** klicken oder **"Console Connection → Launch Cloud Shell connection"**
3. Terminal öffnet sich im Browser, du bist direkt in der VM

**Option B — Lokaler SSH (von deinem PC/Mac):**

```bash
chmod 600 ~/Downloads/ssh-key-xxx.key
ssh -i ~/Downloads/ssh-key-xxx.key ubuntu@130.61.xxx.xxx
```
*(130.61.xxx.xxx ersetzen durch deine Public IP)*

---

## Teil 4 — Bootstrap-Script ausführen (3 Min)

In der VM (du siehst `ubuntu@ccrypto-trader:~$`):

```bash
wget https://raw.githubusercontent.com/HULKCOIN/claude-crypto-trader/main/deploy/oracle_bootstrap.sh
chmod +x oracle_bootstrap.sh
sudo ./oracle_bootstrap.sh
```

Das Script läuft 2-3 Min durch. Am Ende siehst du **"Bootstrap FERTIG"** und eine Liste der nächsten Schritte.

---

## Teil 5 — Secrets eintragen (3 Min)

```bash
sudo nano /opt/ccrypto-trader/.env
```

Jede Zeile mit `TODO_` ersetzt du durch deinen echten Wert. Die Werte hast du schon alle in GitHub Secrets — die kannst du 1:1 übernehmen.

**Beispiel wie es aussehen muss:**
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

Checken dass alles läuft:
```bash
sudo systemctl status ccrypto-telegram --no-pager
sudo systemctl status ccrypto-newsbot --no-pager
sudo systemctl list-timers ccrypto-scanner.timer --no-pager
```

Alle sollten **"active (running)"** zeigen.

---

## Teil 7 — Testen

Schreib deinem **Trading-Bot** (`@dein_trading_bot`) auf Telegram direkt:
```
/status
```
→ sollte **in unter 1 Sekunde** antworten.

Schreib in der **News-Gruppe**:
```
/news
```
→ ebenfalls instant.

---

## Logs live mitlesen

```bash
sudo tail -f /opt/ccrypto-trader/logs/telegram.log
sudo tail -f /opt/ccrypto-trader/logs/newsbot.log
```

(Ctrl+C zum Beenden)

---

## Updates einspielen (wenn ich Code pushe)

```bash
cd /opt/ccrypto-trader
sudo git pull
sudo systemctl restart ccrypto-telegram ccrypto-newsbot
```

---

## Troubleshooting

**"Service failed to start"**
```bash
sudo journalctl -u ccrypto-telegram -n 50 --no-pager
```
→ Fehler sagt was fehlt (meistens leere .env-Variable)

**"Bot antwortet gar nicht"**
```bash
sudo systemctl status ccrypto-telegram
sudo tail -20 /opt/ccrypto-trader/logs/telegram.log
```

**"Secrets aktualisieren"**
```bash
sudo nano /opt/ccrypto-trader/.env
sudo systemctl restart ccrypto-telegram ccrypto-newsbot
```

**"Alles neu aufsetzen"**
```bash
sudo systemctl stop ccrypto-scanner.timer ccrypto-telegram ccrypto-newsbot
sudo rm -rf /opt/ccrypto-trader
# dann oracle_bootstrap.sh nochmal ausführen
```

---

## WICHTIG — Sicherheit

- Die **.env** liegt NUR auf deiner VM, nie im Git-Repo, nie in GitHub Secrets, nie bei mir.
- SSH-Private-Key liegt NUR bei dir lokal.
- Oracle VM ist via SSH-Key gesichert (kein Passwort-Login möglich).
- Firewall lässt nur SSH rein (Port 22) — Bots sind outbound-only (Long-Poll zu Telegram).
- **Niemals** API-Keys im Telegram-Chat oder E-Mail posten.
