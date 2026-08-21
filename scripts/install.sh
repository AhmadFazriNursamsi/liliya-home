#!/bin/bash
# ====================================================================
# Script Instalasi Otomatis STB Smart Assistant (Armbian Linux S905X)
# Asisten: BUDI + Hybrid Smart Home Hub & Whisper STT
# ====================================================================

set -e

REAL_USER=${SUDO_USER:-$USER}
USER_HOME=$(eval echo "~$REAL_USER")
INSTALL_DIR=$(pwd)

echo "=========================================================="
echo "🤖 Memulai Instalasi STB Smart Voice Assistant (BUDI)"
echo "   User: $REAL_USER | Direktori: $INSTALL_DIR"
echo "=========================================================="

# 1. Update Paket Sistem & Install Dependensi Linux
echo "📦 [1/4] Menginstal paket sistem & audio driver..."
apt-get update

# Install paket (kompatibel dengan Ubuntu 24.04 Noble / 22.04 Jammy / Debian)
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    portaudio19-dev \
    libasound2-dev \
    alsa-utils \
    pulseaudio-utils \
    mpv \
    ffmpeg \
    curl \
    git

# Tambahkan user ke grup audio
usermod -aG audio "$REAL_USER" 2>/dev/null || true
chmod -R 777 /dev/snd 2>/dev/null || true

# 2. Setup Python Virtual Environment
echo "🐍 [2/4] Menyiapkan Python Virtual Environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Upgrade pip & install packages
./venv/bin/pip install --upgrade pip wheel setuptools
./venv/bin/pip install -r requirements.txt

# 3. Pre-cache Model AI (Whisper Tiny Bahasa Indonesia)
echo "🧠 [3/4] Menyiapkan model Speech-to-Text Bahasa Indonesia..."
./venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')" || true

# 4. Konfigurasi Systemd Service untuk Autostart
echo "⚙️ [4/4] Memasang systemd service agar otomatis jalan saat STB dinyalakan..."

SERVICE_FILE="/etc/systemd/system/stb-assistant.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=STB Smart Voice Assistant & Smart Home Hub (BUDI)
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stb-assistant.service

# Pastikan kepemilikan file benar
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"

echo ""
echo "=========================================================="
echo "🎉 Instalasi Berhasil Selesai 100%!"
echo "=========================================================="
echo "Asisten BUDI sekarang siap digunakan."
echo ""
echo "Perintah berguna di STB:"
echo "▶️ Menjalankan Service:  sudo systemctl start stb-assistant"
echo "⏹️ Menghentikan Service: sudo systemctl stop stb-assistant"
echo "🔄 Restart Service:      sudo systemctl restart stb-assistant"
echo "📜 Pantau Log Live:      sudo journalctl -u stb-assistant -f"
echo "=========================================================="

# Langsung jalankan servicenya sekarang
echo "🚀 Menyalakan asisten BUDI..."
systemctl restart stb-assistant.service
echo "✅ Asisten BUDI aktif! Silakan panggil 'Budi' ke mikrofon USB Anda."
