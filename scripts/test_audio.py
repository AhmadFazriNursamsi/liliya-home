#!/usr/bin/env python3
"""
=====================================================================
Diagnosa & Uji Coba Audio Lengkap (Speaker & Microphone)
STB Smart Voice Assistant (BUDI)
=====================================================================
"""

import math
import os
import struct
import sys
import time
import numpy as np
import pyaudio

def play_test_tone(p, device_index=None):
    print("\n[1/3] 🔊 MENGUJI SPEAKER (Memutar 3 Nada Beep Melodi)...")
    sample_rate = 44100
    tones = [(523, 0.15), (659, 0.15), (784, 0.25)] # C5, E5, G5
    
    for freq, duration in tones:
        num_samples = int(duration * sample_rate)
        raw_audio = bytearray()
        for i in range(num_samples):
            env = 1.0
            if i < 200: env = i / 200.0
            elif i > num_samples - 200: env = (num_samples - i) / 200.0
            sample = 0.4 * env * math.sin(2 * math.pi * freq * (i / sample_rate))
            raw_audio += struct.pack('<h', int(sample * 32767.0))
            
        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                output=True,
                output_device_index=device_index
            )
            stream.write(bytes(raw_audio))
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"      ⚠️ Gagal output ke device {device_index}: {e}")
            return False
            
        time.sleep(0.05)
        
    print("      ✅ Nada berhasil dikirim ke speaker!")
    return True

def test_mic_record_and_replay(p, input_idx=None, output_idx=None):
    print("\n[2/3] 🎙️ MENGUJI MIKROFON (Merekam 4 Detik Suara Anda)...")
    print("      👉 Silakan BICARA SEKARANG (contoh: 'Halo Budi tes satu dua tiga')...\n")
    
    native_rate = 48000
    chunk_size = int(native_rate * 0.08) # 3840
    
    stream_in = None
    for r in [48000, 16000, 44100]:
        try:
            stream_in = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=r,
                input=True,
                input_device_index=input_idx,
                frames_per_buffer=int(r * 0.08)
            )
            native_rate = r
            chunk_size = int(r * 0.08)
            break
        except Exception:
            continue
            
    if not stream_in:
        print("      ❌ Gagal membuka mikrofon!")
        return
        
    frames_native = []
    start_t = time.time()
    
    while time.time() - start_t < 4.0:
        data = stream_in.read(chunk_size, exception_on_overflow=False)
        arr = np.frombuffer(data, dtype=np.int16)
        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
        frames_native.append(data)
        
        bars = int(min(40, rms / 10))
        meter = "█" * bars + "-" * (40 - bars)
        sys.stdout.write(f"\r      [VU-Meter]: [{meter}] Level: {rms:5.1f}")
        sys.stdout.flush()
        time.sleep(0.02)
        
    stream_in.stop_stream()
    stream_in.close()
    print("\n\n      ✅ Perekaman selesai!")
    
    print("\n[3/3] 🔊 MEMUTAR ULANG SUARA REKAMAN ANDA KE SPEAKER...")
    print("      (Dengarkan speaker Anda sekarang!)\n")
    time.sleep(0.5)
    
    try:
        stream_out = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=native_rate,
            output=True,
            output_device_index=output_idx
        )
        for f in frames_native:
            stream_out.write(f)
            
        stream_out.stop_stream()
        stream_out.close()
        print("      🎉 Selesai! Jika Anda mendengar suara Anda sendiri, mic & speaker 100% SEHAT!")
    except Exception as e:
        print(f"      ⚠️ Gagal memutar ke speaker: {e}")

def main():
    print("=" * 65)
    print("🛠️  STB Audio Diagnostic Tool (Asisten BUDI)")
    print("=" * 65)
    
    p = pyaudio.PyAudio()
    try:
        # 1. Tes nada speaker
        play_test_tone(p)
        time.sleep(0.5)
        # 2. Tes rekam & putar ulang suara
        test_mic_record_and_replay(p)
    finally:
        p.terminate()
        
    print("\n" + "=" * 65)

if __name__ == "__main__":
    main()
