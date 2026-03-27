#!/bin/bash
# ============================================
#  Kalshi Weather Bot — Server Setup Script
#  Run this ON the DigitalOcean droplet
# ============================================
set -e

echo "=== Kalshi Weather Bot Server Setup ==="

# Update system
echo "[1/6] Updating system..."
apt update -y && apt upgrade -y

# Install Python 3.12 + pip
echo "[2/6] Installing Python..."
apt install -y python3.12 python3.12-venv python3-pip git

# Create bot directory
echo "[3/6] Setting up bot directory..."
mkdir -p /opt/kalshi-bot
cd /opt/kalshi-bot

echo "[4/6] Creating virtual environment..."
python3.12 -m venv venv
source venv/bin/activate

echo "[5/6] Installing dependencies..."
pip install --upgrade pip
pip install flask httpx python-dotenv cryptography requests

# Create systemd service for auto-restart
echo "[6/6] Creating systemd service..."
cat > /etc/systemd/system/kalshi-bot.service << 'EOF'
[Unit]
Description=Kalshi Weather Trading Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/kalshi-bot
ExecStart=/opt/kalshi-bot/venv/bin/python dashboard.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kalshi-bot

echo ""
echo "=== Setup Complete ==="
echo "Now upload your bot files to /opt/kalshi-bot/"
echo "Then run: systemctl start kalshi-bot"
echo "Dashboard will be at http://YOUR_IP:5050"
echo ""
