# 🐳 Panduan Menjalankan BUDI Voice Assistant di Docker

Seluruh sistem **STB Smart Voice Assistant & Smart Home Display (BUDI)** telah dibungkus rapi ke dalam kontainer **Docker** yang mendukung arsitektur **ARM64 (STB B860H / HG680P / Raspberry Pi)** maupun **AMD64 (PC / Laptop)**.

---

## 📁 Berkas Docker yang Tersedia:
- [`Dockerfile`](Dockerfile) : Berkas instruksi build image mandiri lengkap dengan dependensi PortAudio, MPV, FFmpeg, Vosk, dan Whisper.
- [`docker-compose.yml`](docker-compose.yml) : Konfigurasi siap pakai dengan passthrough perangkat suara `/dev/snd` dan volume persisten.
- [`.dockerignore`](.dockerignore) : Memastikan virtual environment dan berkas cache tidak ikut ter-build.

---

## 🚀 Cara Menjalankan di STB (Armbian Linux / Ubuntu):

### 1. Jalankan Menggunakan Docker Compose (Sangat Direkomendasikan)
Cukup jalankan satu perintah di folder project:
```bash
docker compose up -d --build
```
> Kontainer akan otomatis membuat image, menghubungkan mikrofon & speaker hardware, dan mengaktifkan Web Dashboard di port `8080`.

### 2. Melihat Log Secara Realtime:
```bash
docker compose logs -f
```

### 3. Menghentikan Kontainer:
```bash
docker compose down
```

---

## 📦 Cara Upload / Push Image ke Docker Hub (Jika Ingin Dibagikan):

Jika Anda ingin menyimpan image ini di **Docker Hub** agar di STB tinggal `docker pull`:

1. **Login ke Docker Hub**:
   ```bash
   docker login
   ```
2. **Build Image dengan Nama Anda**:
   ```bash
   docker build -t username_docker_anda/budi-smart-assistant:latest .
   ```
3. **Upload ke Docker Hub**:
   ```bash
   docker push username_docker_anda/budi-smart-assistant:latest
   ```
4. **Di STB Tinggal Jalankan**:
   ```bash
   docker run -d \
     --name budi-assistant \
     --restart unless-stopped \
     --network host \
     --device /dev/snd:/dev/snd \
     -v $(pwd)/config.yaml:/app/config.yaml \
     -v $(pwd)/data:/app/data \
     username_docker_anda/budi-smart-assistant:latest
   ```

---

## ⚙️ Konfigurasi Volume & Kustomisasi:
- **`config.yaml`**: Anda dapat langsung mengubah API Key Gemini, nama asisten, atau URL Firebase di file `config.yaml` tanpa perlu build ulang Docker.
- **`data/`**: Status saklar lampu dan pembacaan sensor smart home akan tetap tersimpan aman di folder `data/` meskipun kontainer di-restart.
- **`Web UI`**: Buka browser di `http://<IP_STB>:8080`.
