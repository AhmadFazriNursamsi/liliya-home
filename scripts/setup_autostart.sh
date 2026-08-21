#!/bin/bash
set -e

echo "=========================================================="
echo "🚀 Memasang Auto-Start Permanen STB Assistant & Cloudflare"
echo "=========================================================="

CF_PATH=$(which cloudflared || echo "/usr/local/bin/cloudflared")

# 1. Pastikan stb-assistant enabled
systemctl enable stb-assistant.service || true

# 2. Buat systemd service untuk Cloudflare Tunnel
cat <<EOF > /etc/systemd/system/cloudflared-auto.service
[Unit]
Description=Cloudflare Tunnel Auto-Start
After=network-online.target stb-assistant.service
Wants=network-online.target

[Service]
Type=simple
User=fazri
ExecStart=$CF_PATH tunnel --url http://127.0.0.1:8080
Restart=always
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 3. Reload dan aktifkan
systemctl daemon-reload
systemctl enable cloudflared-auto.service
systemctl restart cloudflared-auto.service
systemctl restart stb-assistant.service

echo ""
echo "=========================================================="
echo "✅ SEMUA SERVICE BERHASIL DISETEL AUTO-START!"
echo "   1. Asisten BUDI : [ACTIVE & AUTO-START]"
echo "   2. Cloudflare   : [ACTIVE & AUTO-START]"
echo "=========================================================="
echo "Untuk melihat URL Cloudflare kapan saja, jalankan:"
echo "  sudo journalctl -u cloudflared-auto -n 20 | grep trycloudflare"
echo "=========================================================="
