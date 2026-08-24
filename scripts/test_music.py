#!/usr/bin/env python3
"""
Test Standalone Pemutar Musik YouTube & Radio di STB
"""

import shutil
import subprocess
import time
from core.audio_hw import find_active_playback_device, auto_unmute_hardware_audio

def main():
    print("=" * 65)
    print("🎵 UJI COBA PEMUTAR MUSIK YOUTUBE DI STB")
    print("=" * 65)

    auto_unmute_hardware_audio()
    target_dev = find_active_playback_device()
    print(f"[1] Target Device Output: {target_dev}")

    # 1. Cek yt-dlp
    yt_dlp_path = shutil.which("yt-dlp")
    print(f"[2] Lokasi yt-dlp: {yt_dlp_path or 'TIDAK DITEMUKAN ❌'}")

    # 2. Cek mpv & ffplay
    print(f"[3] Lokasi mpv   : {shutil.which('mpv') or 'TIDAK DITEMUKAN ❌'}")
    print(f"[4] Lokasi ffplay: {shutil.which('ffplay') or 'TIDAK DITEMUKAN ❌'}")

    query = "sheila on 7 sephia audio"
    print(f"\n[5] Mencoba mencari stream URL YouTube untuk: '{query}'...")
    
    cmd_url = ["yt-dlp", "-f", "ba/b", "-g", "--default-search", "ytsearch", f"ytsearch1:{query}"]
    try:
        res = subprocess.run(cmd_url, capture_output=True, text=True, timeout=12)
        stream_url = res.stdout.strip().splitlines()[0] if res.stdout else ""
        if stream_url:
            print(f"    ✅ Berhasil mendapatkan Stream URL!")
            print(f"    🔗 URL: {stream_url[:60]}...")
            
            print(f"\n[6] Memutar lagu selama 6 detik ke speaker ({target_dev})...")
            play_cmd = [
                "mpv",
                "--no-video",
                "--ao=alsa",
                f"--audio-device=alsa/{target_dev}",
                "--volume=50",
                stream_url
            ]
            print(f"    ▶️ Menjalankan: {' '.join(play_cmd[:5])} ...")
            proc = subprocess.Popen(play_cmd)
            time.sleep(6)
            proc.terminate()
            print("    ✅ Uji coba pemutaran selesai!")
            return
        else:
            print(f"    ❌ yt-dlp gagal mendapatkan URL: {res.stderr}")
    except Exception as e:
        print(f"    ❌ Error saat mencari URL: {e}")

    # Coba Radio Online sebagai perbandingan
    print("\n[7] Mencoba memutar Radio Prambors FM sebagai alternatif...")
    radio_url = "https://stream.radiojar.com/7csmg90fuqruv"
    play_cmd = [
        "mpv",
        "--no-video",
        "--ao=alsa",
        f"--audio-device=alsa/{target_dev}",
        "--volume=50",
        radio_url
    ]
    proc = subprocess.Popen(play_cmd)
    time.sleep(6)
    proc.terminate()
    print("    ✅ Uji coba radio selesai!")

if __name__ == "__main__":
    main()
