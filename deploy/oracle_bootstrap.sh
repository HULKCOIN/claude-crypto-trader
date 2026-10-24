#!/usr/bin/env bash
# Bootstrap-Script für Oracle Cloud Always Free VM (Ubuntu 22.04/24.04 ARM Ampere A1).
#
# AUSFÜHREN AUF DER VM (nach SSH-Login) als Root bzw. mit sudo:
#   wget https://raw.githubusercontent.com/HULKCOIN/claude-crypto-trader/main/deploy/oracle_bootstrap.sh
#   chmod +x oracle_bootstrap.sh
#   sudo ./oracle_bootstrap.sh
#
# Was es macht:
#   1. System-Pakete installieren
#   2. Repo nach /opt/ccrypto-trader klonen
#   3. venv + Python-Dependencies
#   4. systemd-Units installieren (noch NICHT starten)
#   5. .env-Template vorbereiten
#
# NACH dem Script musst du:
#   a) /opt/ccrypto-trader/.env befüllen (siehe .env.template)
#   b) sudo systemctl enable --now ccrypto-scanner.timer ccrypto-telegram.service ccrypto-newsbot.service

set -euo pipefail
exec > >(tee -a /var/log/ccrypto-bootstrap.log) 2>&1

echo "[$(date -u +%FT%TZ)] Oracle Cloud Bootstrap gestartet"

# Oracle Ubuntu kommt standard mit snap-based firewall. Outbound HTTPS geht immer.
# Inbound: nur SSH, keine Ports öffnen — Bot ist outbound-only Long-Poll.

# 1. System aktualisieren
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-pip python3-venv git curl ca-certificates tzdata

# Zeitzone auf Berlin (optional, hilft bei Logs lesen)
timedatectl set-timezone Europe/Berlin || true

# 2. Repo klonen
INSTALL_DIR=/opt/ccrypto-trader
if [[ ! -d "$INSTALL_DIR" ]]; then
    git clone https://github.com/HULKCOIN/claude-crypto-trader.git "$INSTALL_DIR"
    echo "[bootstrap] Repo geklont"
else
    cd "$INSTALL_DIR" && git pull
    echo "[bootstrap] Repo aktualisiert"
fi

# 3. venv + dependencies
cd "$INSTALL_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "[bootstrap] Python-Dependencies installiert"

# 4. config.json aus example
if [[ ! -f config.json ]]; then
    cp config.example.json config.json
    echo "[bootstrap] config.json angelegt (aus example)"
fi

# 5. systemd Services installieren (aber noch NICHT starten!)
cp deploy/systemd/*.service /etc/systemd/system/
cp deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
echo "[bootstrap] systemd-Units installiert"

# 6. Verzeichnisse + Permissions
mkdir -p "$INSTALL_DIR/bridge" "$INSTALL_DIR/logs" "$INSTALL_DIR/data/snapshots" "$INSTALL_DIR/data/gemini_logs"
chmod +x deploy/*.sh

# 7. .env-Template kopieren (falls noch nicht existiert)
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/deploy/.env.template" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    echo "[bootstrap] .env-Template angelegt → MUSST du jetzt editieren!"
fi

# 8. Markiere Bootstrap als fertig
touch /opt/ccrypto-bootstrap-done

echo ""
echo "============================================================"
echo "[$(date -u +%FT%TZ)] Bootstrap FERTIG"
echo "============================================================"
echo ""
echo "NÄCHSTE SCHRITTE:"
echo ""
echo "1) Secrets eintragen:"
echo "   sudo nano /opt/ccrypto-trader/.env"
echo "   → alle TODO-Werte durch deine echten Secrets ersetzen"
echo ""
echo "2) Services starten:"
echo "   sudo systemctl enable --now ccrypto-scanner.timer"
echo "   sudo systemctl enable --now ccrypto-telegram.service"
echo "   sudo systemctl enable --now ccrypto-newsbot.service"
echo ""
echo "3) Status prüfen:"
echo "   sudo systemctl status ccrypto-telegram ccrypto-newsbot"
echo "   sudo tail -f /opt/ccrypto-trader/logs/telegram.log"
echo "   sudo tail -f /opt/ccrypto-trader/logs/newsbot.log"
echo ""
echo "4) Updates einspielen (später):"
echo "   cd /opt/ccrypto-trader && sudo git pull && sudo systemctl restart ccrypto-telegram ccrypto-newsbot"
echo ""
