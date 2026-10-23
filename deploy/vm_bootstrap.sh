#!/usr/bin/env bash
# Läuft AUTOMATISCH beim ersten Boot der GCP VM (startup-script metadata).
# Installiert alles was nötig ist, legt /opt/ccrypto-trader an, aber startet
# noch NICHTS (Secrets fehlen noch — User muss .env anlegen).

set -euo pipefail
exec > >(tee -a /var/log/ccrypto-bootstrap.log) 2>&1

echo "[$(date -u +%FT%TZ)] Bootstrap gestartet"

# 1. System aktualisieren
apt-get update
apt-get install -y python3 python3-pip python3-venv git curl ca-certificates

# 2. Repo klonen
INSTALL_DIR=/opt/ccrypto-trader
if [[ ! -d "$INSTALL_DIR" ]]; then
    git clone https://github.com/HULKCOIN/claude-crypto-trader.git "$INSTALL_DIR"
else
    cd "$INSTALL_DIR" && git pull
fi

# 3. venv + dependencies
cd "$INSTALL_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. config.json aus example
if [[ ! -f config.json ]]; then
    cp config.example.json config.json
fi

# 5. systemd Services installieren (aber noch NICHT starten!)
cp deploy/systemd/*.service /etc/systemd/system/
cp deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

# 6. Verzeichnisse + Permissions
mkdir -p "$INSTALL_DIR/bridge" "$INSTALL_DIR/logs" "$INSTALL_DIR/data/snapshots"
chmod +x deploy/*.sh

# 7. Markiere Bootstrap als fertig
touch /opt/ccrypto-bootstrap-done

echo "[$(date -u +%FT%TZ)] Bootstrap fertig. Jetzt /opt/ccrypto-trader/.env mit Secrets befüllen und Services starten."
