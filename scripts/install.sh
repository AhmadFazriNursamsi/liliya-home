#!/bin/bash
# ====================================================================
# Script Instalasi Otomatis STB Smart Assistant (Armbian Linux S905X)
# ====================================================================

set -e

echo "=========================================================="
echo "🚀 Memulai Instalasi STB Smart Voice Assistant (Gemini AI)"
echo "=========================================================="

# 1. Update Paket Sistem & Install Dependensi Linux
echo "📦 [1/5] Menginstal paket sistem & audio driver..."
sudo apt update
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    portaudio19-dev \
    libasound2-dev \
    alsa-utils \
    mpv \
    ffmpeg \
    curl \
    git

# 2. Setup Python Virtual Environment
INSTALL_DIR=$(pwd)
echo "🐍 [2/5] Membuat Python Virtual Environment di $INSTALL_DIR/venv ..."

python3 -m venv venv
source venv/bin/activate

# 3. Install Python Dependencies
echo "📥 [3/5] Menginstal dependensi Python dari requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Download Model Wake Word
echo "🧠 [4/5] Mengunduh model Wake Word (openwakeword)..."
python3 -c "import openwakeword; openwakeword.utils.download_models()" || true

# 5. Konfigurasi Systemd Service untuk Autostart
echo "⚙️ [5/5] Memasang systemd service agar asisten otomatis jalan saat STB hidup..."

SERVICE_FILE="/etc/systemd/system/stb-assistant.service"

sudo bash -c "cat <<EOF > $SERVICE_FILE
[Unit]
Description=STB Gemini Smart Voice Assistant (Alexa Clone)
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable stb-assistant.service

echo ""
echo "=========================================================="
echo "✅ Instalasi Berhasil Selesai!"
echo "=========================================================="
echo "Langkah selanjutnya:"
echo "1. Edit file 'config.yaml' dan masukkan Gemini API Key Anda:"
echo "   nano config.yaml"
echo ""
echo "2. Uji coba audio mikrofon & speaker:"
echo "   python3 scripts/test_audio.py"
echo ""
echo "3. Jalankan manual untuk pertama kali:"
echo "   ./venv/bin/python main.py"
echo ""
echo "4. Atau jalankan sebagai background service otomatis:"
echo "   sudo systemctl start stb-assistant"
echo "   sudo journalctl -u stb-assistant -f"
echo "=========================================================="
