# BUDI: APK Android, Saklar Manual & CCTV

## Arsitektur

```
HP (APK BUDI / browser)
   │  https://smarthome.laravelia.my.id  (Cloudflare Tunnel → localhost:8080, wajib login)
   │  http://192.168.1.3:8080            (WiFi rumah / Tailscale, tanpa login)
   ▼
BUDI (aiohttp :8080) ── login, saklar, API rekaman, proxy live view, pan-tilt ONVIF
   │
   ├── go2rtc (127.0.0.1:1984 / :8554) ── 1 koneksi RTSP ke kamera, dibagi untuk live & rekam
   └── cctv-record@cam1 (ffmpeg -c copy) ── /srv/cctv/cam1/YYYYMMDD_HHMMSS.mp4 (10 menit/segmen)
        └── cctv-cleanup.timer (tiap jam) ── hapus rekaman > CCTV_RETENTION_DAYS / bila disk penuh
```

API konfigurasi go2rtc **tidak** diekspos. BUDI hanya meneruskan `video-stream.js`, `video-rtc.js`
dan `api/ws` untuk stream yang terdaftar di `config.yaml`.

## Konfigurasi `config.yaml`

```yaml
web_auth:
  enabled: true
  password: "ganti-password-ini"
  session_days: 60
  trusted_networks: ["192.168.0.0/16", "100.64.0.0/10"]   # akses tanpa login
  api_tokens: []                                           # untuk ESP32: header X-Api-Key

cctv:
  enabled: true
  go2rtc_url: "http://127.0.0.1:1984"
  recordings_dir: "/srv/cctv"
  segment_seconds: 600
  cameras:
    - id: cam1                 # harus sama dengan nama stream go2rtc & service cctv-record@cam1
      name: "Tapo C200"
      live_stream: cam1_sd     # 720p, hemat kuota
      hd_stream: cam1          # 1080p
      onvif: { host: 192.168.1.4, port: 2020, username: "...", password: "..." }
```

## Pasang service CCTV

```bash
sudo install -d -m 750 -o root -g fazri /etc/budi-cctv
sudo install -m 640 -o root -g fazri deploy/cctv/go2rtc.example.yaml /etc/budi-cctv/go2rtc.yaml   # isi USER/PASSWORD kamera
sudo install -m 640 -o root -g fazri deploy/cctv/record.env /etc/budi-cctv/
sudo install -m 755 deploy/cctv/cctv-record deploy/cctv/cctv-cleanup /usr/local/bin/
sudo install -m 644 deploy/cctv/*.service deploy/cctv/*.timer /etc/systemd/system/
sudo install -d -m 755 -o fazri -g fazri /srv/cctv
sudo systemctl daemon-reload
sudo systemctl enable --now go2rtc cctv-record@cam1 cctv-cleanup.timer
```

Lama penyimpanan diatur di `/etc/budi-cctv/record.env` (`CCTV_RETENTION_DAYS`, `CCTV_MIN_FREE_GB`).
Tapo C200 ±1,6 Mbps ≈ 17 GB/hari per kamera.

## Kunci tanda tangan APK

APK dibangun oleh GitHub Actions (`.github/workflows/android-apk.yml`) dan dirilis di tab **Releases**.
Workflow membutuhkan 4 secret repo:

| Secret | Isi |
|---|---|
| `BUDI_KEYSTORE_BASE64` | isi file keystore dalam base64 |
| `BUDI_KEYSTORE_PASSWORD` | password keystore |
| `BUDI_KEY_ALIAS` | `budi` |
| `BUDI_KEY_PASSWORD` | password key |

Keystore yang sama **harus** dipakai untuk setiap rilis, kalau tidak update APK tidak bisa dipasang di atas versi lama.
