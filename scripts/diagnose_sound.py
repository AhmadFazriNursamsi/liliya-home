#!/usr/bin/env python3
"""
Diagnostic Audio Deep-Scan
Mendeteksi jalur speaker dan mikrofon fisik pada STB
"""

import os
import subprocess
import time
import wave
import numpy as np

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return res.stdout.strip(), res.stderr.strip(), res.returncode
    except Exception as e:
        return "", str(e), 1

def main():
    print("=" * 65)
    print("🔍 DIAGNOSA MENDALAM AUDIO STB (AB13X & S905X)")
    print("=" * 65)
    
    # 0. Hentikan service sebentar agar port audio tidak terkunci
    print("\n[Langkah 0] Melepaskan kunci perangkat audio...")
    run_cmd("sudo systemctl stop stb-assistant.service 2>/dev/null")
    time.sleep(0.5)
    
    # 1. Buat file WAV nada bunyi 1 detik
    wav_path = "/tmp/test_beep.wav"
    sr = 48000
    t = np.linspace(0, 1.0, sr, False)
    # Nada 880Hz (A5) jernih dan keras
    sig = (np.sin(2 * np.pi * 880 * t) * 32760).astype(np.int16)
    
    with wave.open(wav_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
        
    print("\n[Langkah 1] Menguji Output Speaker di semua kartu suara...")
    
    devices_to_test = [
        ("plughw:0,0", "USB Audio Dongle (AB13X)"),
        ("plughw:1,0", "Built-in STB AV Jack / HDMI (S905X)")
    ]
    
    for dev, desc in devices_to_test:
        print(f"\n👉 Mencoba memutar bunyi di: {dev} [{desc}]...")
        out, err, code = run_cmd(f"aplay -D {dev} {wav_path} 2>&1")
        if code == 0:
            print(f"   ✅ Sinyal audio terkirim ke {dev}!")
        else:
            print(f"   ⚠️ Error di {dev}: {err or out}")
            
    print("\n" + "-" * 50)
    print("[Langkah 2] Menguji Rekaman Mikrofon...")
    rec_path = "/tmp/mic_record_test.wav"
    
    print("👉 Merekam 3 detik dari plughw:0,0 (USB Mic)...")
    print("   📢 SILAKAN BERBICARA KERAS DI DEPAN MIC SEKARANG...")
    
    out, err, code = run_cmd(f"arecord -D plughw:0,0 -f S16_LE -r 48000 -c 1 -d 3 {rec_path} 2>&1")
    if code == 0 and os.path.exists(rec_path):
        with wave.open(rec_path, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            arr = np.frombuffer(frames, dtype=np.int16)
            rms = np.sqrt(np.mean(arr.astype(np.float32) ** 2))
            max_val = np.max(np.abs(arr))
            
        print(f"   ✅ Rekaman berhasil! Ukuran: {len(frames)} bytes")
        print(f"   📊 Rata-rata Kekuatan Suara (RMS): {rms:.1f} (Max Peak: {max_val})")
        
        if max_val > 500:
            print("   🎉 MIKROFON BERFUNGSI BAGUS! (Suara fisik tertangkap).")
        else:
            print("   ⚠️ Sinyal mikrofon sangat rendah (hanya hening/noise latar).")
            print("      Pastikan jack mic tertancap penuh ke lubang mic (pink/mic) di USB dongle.")
            
        # Coba putar kembali rekaman ke plughw:0,0
        print("\n👉 Memutar ulang rekaman ke USB Audio (plughw:0,0)...")
        run_cmd(f"aplay -D plughw:0,0 {rec_path} 2>&1")
        
    else:
        print(f"   ❌ Gagal merekam: {err or out}")
        
    print("\n" + "=" * 65)
    print("Diagnosa selesai. Jalankan 'sudo systemctl start stb-assistant.service' jika sudah selesai.")
    print("=" * 65)

if __name__ == "__main__":
    main()
