#!/bin/bash
# CiteVerify — Deploy / update
# Run: ssh root@64.23.203.101 'bash -s' < scripts/deploy.sh
set -euo pipefail

APP_DIR=/opt/citeverify

echo "=== CiteVerify Deploy ==="

cd "$APP_DIR"

# ─── Pull latest code ────────────────────────────────────────────────────
echo "[1/4] Pulling latest code..."
git pull --ff-only origin main

# ─── Check .env ──────────────────────────────────────────────────────────
if ! grep -q 'ANTHROPIC_API_KEY=sk-' .env 2>/dev/null; then
    echo "ERROR: ANTHROPIC_API_KEY not set in .env — edit it first!"
    exit 1
fi

# ─── Nginx HTTP-only config (no SSL yet) ─────────────────────────────────
echo "[2/4] Configuring nginx for HTTP..."
mkdir -p nginx/ssl nginx/certbot
# Create self-signed cert so nginx can start (will be replaced by Let's Encrypt)
if [ ! -f nginx/ssl/fullchain.pem ]; then
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout nginx/ssl/privkey.pem \
        -out nginx/ssl/fullchain.pem \
        -subj "/CN=citeverify" 2>/dev/null
    echo "  (created self-signed cert — replace with Let's Encrypt when you have a domain)"
fi

# ─── Build and deploy ────────────────────────────────────────────────────
echo "[3/4] Building Docker images..."
docker compose build

echo "[4/4] Starting services..."
docker compose up -d

# ─── Health check ────────────────────────────────────────────────────────
echo "Waiting for app to start..."
for i in $(seq 1 30); do
    if curl -sf http://localhost/api/health >/dev/null 2>&1; then
        echo ""
        echo "=== Deploy successful! ==="
        IP=$(curl -sf http://checkip.amazonaws.com 2>/dev/null || hostname -I | awk '{print $1}')
        echo "  App: http://$IP"
        echo "  Health: http://$IP/api/health"
        docker compose ps
        exit 0
    fi
    sleep 2
    printf "."
done

echo ""
echo "ERROR: App did not become healthy in 60s"
docker compose logs --tail=30
exit 1
