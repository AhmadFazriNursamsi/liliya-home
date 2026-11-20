# Liliya / BUDI — panduan untuk Claude Code

Hub rumah pintar berbahasa Indonesia yang berjalan di STB Armbian. Python aiohttp di `core/`, web UI di `web/`, aplikasi Android (WebView) di `android/`, firmware kunci pintu ESP32 di `firmware/door_lock/`.

**Repo ini PUBLIK.** Jangan pernah meng-commit password, token, local_key Tuya, UID kartu RFID, atau isi `config.yaml`. Rahasia hanya ada di STB (`config.yaml`, izin 600) dan `firmware/door_lock/secrets.h` (gitignored).

## Cara kerja dengan user
- Bahasa Indonesia, jelaskan dengan bahasa awam.
- **Setiap revisi: commit + push** ke `main`.
- Tindakan yang mengubah perangkat fisik di rumah (lampu, pintu, speaker kamera) atau server produksi: minta izin dulu.

## Akses STB
- `ssh stb-ts` (lewat Tailscale). Di WiFi rumah, STB juga ada di 192.168.1.5.
- Kode hub ada di `/home/fazri/stb-gemini-assistant` milik user `fazri`. User SSH tidak bisa `cd` ke folder itu, jadi pakai:
  - `sudo -n -u fazri git -C /home/fazri/stb-gemini-assistant <perintah>`
  - Python: `sudo -n -u fazri sh -c "cd /home/fazri/stb-gemini-assistant && ./venv/bin/python -I -"` (skrip lewat stdin)
- Clone di STB punya perubahan lokal yang disengaja (`data/smart_home_db.json`), plus satu stash WIP talk lama. **Jangan `reset --hard`.**
- Saat membaca config, jangan pernah mencetak nilai rahasia.

## Deploy
- **Server:** push ke GitHub, lalu `sudo -n -u fazri git -C /home/fazri/stb-gemini-assistant pull -q && sudo -n systemctl restart stb-assistant`. Tunggu `curl -s -o /dev/null http://127.0.0.1:8080/login` berhasil. File statis `web/` langsung aktif tanpa restart; naikkan `?v=` di `web/index.html` bila JS/CSS berubah.
- **APK:** GitHub Actions membangun otomatis setiap push ke `main` dan menerbitkan Release `apk-vX.Y.Z`. Cek dengan `gh run list` / `gh release list`.
- Layanan terkait: `stb-assistant` (hub, port 8080), `go2rtc` (CCTV, 127.0.0.1:1984 / rtsp 8554), `cloudflared` (tunnel, ingress `localhost:*`), nginx (situs lain di 127.0.0.1:8090–8092).

## Status pekerjaan (update terakhir 20 Nov 2026)

### Selesai
- Hardening keamanan: cek Origin POST/WS, `_safe_next`, field perangkat terlindungi, host ONVIF dikunci, guard tunnel, APK hanya `content://`, header `X-Liliya-Hub`, backup Android dimatikan. Riwayat git sudah ditulis ulang (clone ulang bila punya salinan lama).
- CCTV: **bicara langsung** (tahan = bicara, ketuk = terus) dan **panggilan dua arah** (tombol telepon). Jalurnya: WebSocket `/api/cctv/{cam}/talk/live`, lalu PCM 16 kHz, lalu `/api/cctv-feed/{token}` (hanya loopback), lalu go2rtc, lalu backchannel Tapo. Sambungan pertama ±6 dtk. APK ≥ v1.0.23.
- APK: **pemutar video/musik** (`MediaActivity`, Media3 ExoPlayer 1.4.1). Dari WhatsApp/File Manager, pilih "Buka dengan"/"Bagikan" lalu Liliya, dan file diputar langsung di HP tanpa diunggah. Hanya `content://`. Ide lanjutan: tombol "Putar di TV".
- Pengecekan status lampu setelah perintah (`core/tuya_bridge.py` `_verify_state`). Diam untuk lampu BLE karena Tuya Cloud tidak melaporkan statusnya.

### Sedang berjalan: lepas dari Tuya Cloud (free tier habis ±Jan 2027)
- **Titik kembali:** tag git `sebelum-tuya-lokal`, dan di STB `config.yaml.bak-20261112-153051` serta `config.yaml.bak-20261112-154550-sebelum-kunci`.
- Gateway Bluetooth Tuya (wg2) ada di LAN 192.168.1.3, protokol 3.4. Kunci lokal gateway dan node_id 4 lampu **sudah disimpan** di `config.yaml` STB, bagian `tuya_local`.
- **Kendali lokal BERHASIL (20 Nov 2026)**: ruang tamu dan halaman depan terbukti mati/nyala dari STB tanpa cloud.
  - Kirim **tanpa menunggu balasan** (`getresponse=False`). Bila menunggu, tiap perintah tertahan 5 dtk dan antre, sehingga lampu terlihat "tidak merespons".
  - Lampu Bluetooth ini memakai **DP 1** (`switch_led`), bukan DP 20. Cek pemetaan DP lewat cloud `/v2.0/cloud/thing/{id}/shadow/properties`.
  - Perintah dikirim sebagai CONTROL_NEW (cmd 13, `protocol:5`, `cid`=node_id). Gateway **tidak pernah membalas** perintah; `status()` sub-device ditolak.
  - Status nyala/mati di Tuya Cloud untuk lampu ini basi/salah, jadi `sync_now` mengabaikan state cloud untuk perangkat di `tuya_local`.
- `core/tuya_bridge.py` sekarang: nyala/mati lewat gateway lokal dulu (coba ulang 3x, `tuya_local.retries`), Tuya Cloud sebagai cadangan. Kecerahan juga lokal: DP 3 `bright_value`, 10-1000 (persen x 10), sudah diuji di ruang tamu. `switch_dp`/`bright_dp` per perangkat bisa diatur di `tuya_local.devices`.
- **Halaman depan sinyal Bluetooth-nya lemah**: Smart Life pun perlu ±3 klik. Di `config.yaml` diberi `repeat: 4`, `repeat_gap: 2` (kirim ulang, berhenti bila ada perintah baru). Backup: `config.yaml.bak-20261120-sebelum-repeat`. Solusi utama: user akan memindahkan gateway lebih dekat ke halaman depan.
- Koneksi STB ke gateway 192.168.1.3 **putus-sambung** (ARP kadang gagal, broadcast UDP tetap terlihat). Perlu cek sinyal WiFi gateway.
- **Langkah berikutnya:**
  1. Kendali lampu sudah lepas dari cloud. Cloud masih dipakai untuk cadangan, daftar perangkat (Tambah Perangkat), dan status online.
  - Catatan: `bright_value_v2` di cloud BENAR (kode standar, diterjemahkan cloud ke DP 3).

### Ditunda (atas keputusan user)
- Pindah penyimpanan perangkat dari `data/smart_home_db.json` ke **SQLite**. Rencananya tabel `devices`, `device_state`, `device_history`, dan `device_secrets` (kunci terpisah, tidak pernah dikirim ke web/Firebase). Kerjakan setelah kendali lokal Tuya berhasil.
- FCM unsubscribe dan hardening LibreOffice: risiko rendah, pemakaian keluarga.

### Tugas user (ingatkan bila relevan)
- Tes panggilan dua arah di rumah (gema, volume, jeda).
- Isi `WIFI_PASS` dan `DEVICE_TOKEN` di `firmware/door_lock/secrets.h` sebelum flash firmware pintu.
- Simpan salinan `config.yaml` STB di tempat aman (kunci lokal Tuya hanya ada di STB).
