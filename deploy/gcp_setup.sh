#!/usr/bin/env bash
# GCP VM Setup für HULKCOIN Crypto Trader
# -------------------------------------------
# Lege eine always-free e2-micro VM in europe-west3 (Frankfurt) an.
# Von dort ist Binance NICHT geoblockt. Kosten: 0 € nach Trial.
#
# Voraussetzungen:
#   • gcloud CLI installiert (https://cloud.google.com/sdk/docs/install)
#   • Login:   gcloud auth login
#   • Projekt: gcloud config set project <PROJECT_ID>
#
# Ausführen:  bash deploy/gcp_setup.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
VM_NAME="${VM_NAME:-ccrypto-trader}"
REGION="${REGION:-europe-west3}"              # Frankfurt — Binance geht
ZONE="${ZONE:-europe-west3-c}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-micro}"       # always-free berechtigt
DISK_SIZE="${DISK_SIZE:-30GB}"                 # 30GB free tier
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
GITHUB_REPO="${GITHUB_REPO:-https://github.com/HULKCOIN/claude-crypto-trader.git}"

if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ Kein GCP-Projekt gesetzt. Bitte zuerst:"
    echo "   gcloud config set project <PROJECT_ID>"
    exit 1
fi

echo "============================================================"
echo "🚀 HULKCOIN Crypto Trader → GCP Deployment"
echo "============================================================"
echo "Projekt:      $PROJECT_ID"
echo "VM-Name:      $VM_NAME"
echo "Region:       $REGION ($ZONE)"
echo "Maschine:     $MACHINE_TYPE (always-free)"
echo "Disk:         $DISK_SIZE"
echo "Repo:         $GITHUB_REPO"
echo "============================================================"
read -r -p "Fortfahren? [j/N] " ANSWER
if [[ ! "$ANSWER" =~ ^[jJyY]$ ]]; then
    echo "Abgebrochen."
    exit 0
fi

# 1. Benötigte APIs aktivieren
echo ""
echo "🔧 Aktiviere benötigte GCP APIs..."
gcloud services enable compute.googleapis.com --project="$PROJECT_ID"

# 2. Prüfe ob VM schon existiert
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" &>/dev/null; then
    echo "⚠️  VM $VM_NAME existiert bereits in $ZONE. Überspringe create."
else
    echo ""
    echo "🖥️  Erstelle VM $VM_NAME in $ZONE..."
    gcloud compute instances create "$VM_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --image-family="$IMAGE_FAMILY" \
        --image-project="$IMAGE_PROJECT" \
        --boot-disk-size="$DISK_SIZE" \
        --boot-disk-type=pd-standard \
        --tags=ccrypto-trader \
        --metadata=enable-oslogin=TRUE \
        --metadata-from-file=startup-script=deploy/vm_bootstrap.sh
fi

# 3. Firewall-Regel (optional, nur falls du später Webhooks willst)
# Standardmäßig keine eingehenden Verbindungen nötig (Telegram ist outbound-only)

# 4. Zeige SSH-Befehl + externe IP
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
    --project="$PROJECT_ID" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "============================================================"
echo "✅ VM läuft!"
echo "============================================================"
echo "Externe IP: $EXTERNAL_IP"
echo ""
echo "📝 Nächste Schritte:"
echo ""
echo "1. SSH auf die VM:"
echo "   gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
echo ""
echo "2. Dort .env mit deinen Secrets anlegen:"
echo "   cd /opt/ccrypto-trader"
echo "   sudo nano .env"
echo ""
echo "   Inhalt (die Werte von GitHub Secrets kopieren):"
echo "   TELEGRAM_BOT_TOKEN=xxx:yyy..."
echo "   TELEGRAM_CHAT_ID=8398295986"
echo "   BINANCE_API_KEY=..."
echo "   BINANCE_API_SECRET=..."
echo "   ETHERSCAN_API_KEY=..."
echo "   GEMINI_API_KEY=...        # Optional, für LLM-Decider"
echo ""
echo "3. Dienste starten:"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now ccrypto-scanner.timer"
echo "   sudo systemctl enable --now ccrypto-telegram.service"
echo ""
echo "4. Logs anschauen:"
echo "   sudo journalctl -u ccrypto-telegram -f"
echo "   sudo journalctl -u ccrypto-scanner -f"
echo ""
echo "💰 Kosten: 0 € — e2-micro in us-west1/central1/east1 ist always-free."
echo "   WICHTIG: europe-west3 (Frankfurt) ist NICHT in der always-free list!"
echo "   Nach dem Trial-Ende (90 Tage): ca. 6 €/Monat für europe-west3."
echo "   Für always-free: wechsle auf us-west1-b bei VM-Create."
echo ""
echo "📱 Zum Löschen (später, falls du's nicht mehr brauchst):"
echo "   gcloud compute instances delete $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
