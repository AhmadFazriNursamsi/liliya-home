# 🤖 STB Smart Voice Assistant & Smart Display (Alexa / Echo Show Clone)
Transformasi STB Bekas (**ZTE B860H / Fiberhome HG680P - Amlogic S905X**) menjadi **Smart Speaker & Smart Display Mandiri** berbasis **Google Gemini Cloud AI**.

---

## 🌟 Fitur Utama

- 🎙️ **Hands-free Wake Word**: Siaga mendengarkan kata pemicu *"Alexa"* atau *"Hey Jarvis"* via USB Microphone.
- 💤 **Two-Stage Low-Power Idle**: STB berada di mode tidur saat ruangan sepi (CPU < 0.5%, RAM ~40MB, suhu tetap dingin dan kebal kebisingan sekitar).
- 🖥️ **Front-End Smart Display UI**:
  - **Tampilan Web & Smart Display Modern** (mirip Amazon Echo Show / Siri / Google Nest Hub).
  - **Visualizer Orb Futuristik**: Beranimasi otomatis saat *Standby* (biru redup), *Mendengarkan* (cyan menyala), *Berpikir* (ungu berputar), dan *Membacakan Suara* (gelombang equalizer).
  - **Jam Digital, Tanggal & Widget Cuaca Realtime**.
  - **Live Subtitle & Chat History**: Teks ucapan Anda dan jawaban Gemini muncul di layar.
  - **Media Card**: Menampilkan judul lagu YouTube / radio yang sedang diputar.
  - **Input Teks Interaktif**: Bisa mengetik perintah dari HP / Laptop jika tidak ingin bersuara.
- 🧠 **Google Gemini Flash AI**: Otak cerdas multimodal yang memproses perintah suara secara instan tanpa membebani STB.
- 🗣️ **Suara Natural Bahasa Indonesia (Edge-TTS)**: Suara jernih seperti asisten manusia asli (`id-ID-GadisNeural` / `id-ID-ArdiNeural`).
- 🎵 **Music & Radio Streaming**: Putar lagu YouTube atau siaran radio online secara langsung (`yt-dlp` + `mpv`).
- ⏰ **Timer & Alarm Pengingat**: Setel alarm atau timer hitung mundur.
- 🌤️ **Info Cuaca & Jam**: Cek cuaca realtime dan waktu lokal.
- 🚀 **Headless & Autostart (Systemd)**: Otomatis aktif saat STB dicolok listrik.

---

## 🔌 Kebutuhan Perangkat Keras

1. **STB**: ZTE B860H (v1/v2/v5) atau Fiberhome HG680P (Amlogic S905X).
2. **MicroSD Card**: Minimal 8GB Class 10 (sudah terisi Armbian Linux).
3. **USB Microphone**: Mini USB Mic / USB Condenser / USB Conference Speaker.
4. **Speaker**: Speaker aktif colok ke Jack 3.5mm AV STB atau USB Speaker.
5. **Monitor / TV (Opsional)**: Colok ke kabel HDMI jika ingin difungsikan sebagai Smart Display (layar pintar).
6. **Koneksi Internet**: WiFi atau Kabel LAN.

---

## 🚀 Panduan Instalasi Cepat

### 1. Boot STB ke Armbian Linux
- Pastikan STB sudah terinstall Armbian Linux.
- Masuk ke STB via SSH dari terminal komputer Anda:
  ```bash
  ssh root@<IP_STB_ANDA>
  ```

### 2. Salin Folder Proyek Ini ke STB
Salin folder `stb-gemini-assistant` ke direktori `/opt/stb-gemini-assistant` pada STB:
```bash
sudo cp -r stb-gemini-assistant /opt/stb-gemini-assistant
cd /opt/stb-gemini-assistant
```

### 3. Jalankan Script Instalasi Otomatis
```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

### 4. Masukkan Gemini API Key Anda
Buka file `config.yaml`:
```bash
nano config.yaml
```
Ganti baris `api_keys` dengan API Key Gemini Anda:
```yaml
gemini:
  api_keys:
    - "AIzaSy..." # Masukkan API Key Anda di sini
```
Simpan dengan menekan `Ctrl + O`, lalu `Enter`, kemudian keluar dengan `Ctrl + X`.

---

## 🖥️ Mengakses Tampilan Front-End (Web & TV Display)

### A. Dari Browser HP / Laptop / Tablet
Buka browser dan ketik alamat IP STB Anda:
```text
http://<IP_STB_ANDA>:8080
```
*(Contoh: `http://192.168.1.100:8080`)*

### B. Menjadikan Layar TV sebagai Smart Display (Kiosk Mode)
Jika STB dicolok ke TV/Monitor HDMI dan Anda ingin layar TV selalu menampilkan Smart Display:
```bash
chromium --kiosk --noerrdialogs --disable-infobars http://localhost:8080
```

---

## 🧪 Uji Coba Audio & Menjalankan Asisten

1. **Uji Audio**:
   ```bash
   ./venv/bin/python scripts/test_audio.py
   ```
2. **Jalankan Asisten**:
   ```bash
   ./venv/bin/python main.py
   ```

Setelah mendengar suara sapaan *"Asisten siap digunakan"*, cobalah:
- **Panggil**: *"Alexa!"* → (Visualizer di layar akan berubah menyala hijau & berbunyi *Ding!*)
- **Perintah Suara**:
  - 🎵 *"Putar lagu Sheila on 7 Dan di YouTube"*
  - 📻 *"Putar siaran radio Prambors"*
  - ⏹️ *"Hentikan musiknya"*
  - 🌤️ *"Bagaimana cuaca di Jakarta hari ini?"*
  - ⏰ *"Pasang timer 3 menit untuk masak mie"*
  - 🕐 *"Jam berapa sekarang?"*
  - 🔊 *"Kecilkan volume ke 50 persen"*
  - 💬 *"Ceritakan dongeng singkat tentang kancil yang cerdik"*

---

## 🔄 Mengaktifkan Autostart 24/7 (Background Service)

```bash
# Aktifkan dan jalankan background service
sudo systemctl enable stb-assistant
sudo systemctl start stb-assistant

# Cek log realtime
sudo journalctl -u stb-assistant -f
```
