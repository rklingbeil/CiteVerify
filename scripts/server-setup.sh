#!/bin/bash
# CiteVerify — One-time server provisioning
# Run: ssh root@159.89.229.245 'bash -s' < scripts/server-setup.sh
set -euo pipefail

echo "=== CiteVerify Server Setup ==="

# ─── System updates ──────────────────────────────────────────────────────
echo "[1/6] Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# ─── Install Docker ──────────────────────────────────────────────────────
echo "[2/6] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable docker
systemctl start docker

# Install docker compose plugin
apt-get install -y -qq docker-compose-plugin

# ─── Firewall ────────────────────────────────────────────────────────────
echo "[3/6] Configuring firewall..."
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ─── Swap (1 GB RAM is tight for Docker builds) ─────────────────────────
echo "[4/6] Setting up swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ─── Clone repo ──────────────────────────────────────────────────────────
echo "[5/6] Cloning CiteVerify..."
if [ ! -d /opt/citeverify ]; then
    git clone https://github.com/rklingbeil/CiteVerify.git /opt/citeverify
fi

# ─── Create .env placeholder ─────────────────────────────────────────────
echo "[6/6] Creating .env template..."
if [ ! -f /opt/citeverify/.env ]; then
    cat > /opt/citeverify/.env << 'ENVEOF'
ANTHROPIC_API_KEY=
COURTLISTENER_API_TOKEN=
GOVINFO_API_KEY=DEMO_KEY
CLAUDE_MODEL=claude-sonnet-4-20250514
ENVEOF
    echo ">>> IMPORTANT: Edit /opt/citeverify/.env with your API keys before deploying!"
fi

echo ""
echo "=== Setup complete! ==="
echo "Next steps:"
echo "  1. Edit /opt/citeverify/.env with your API keys"
echo "  2. Run: ssh root@159.89.229.245 'bash -s' < scripts/deploy.sh"
