#!/usr/bin/env python3
"""
=====================================================================
Audio Diagnostic & Calibration Tool untuk STB
=====================================================================
"""

import math
import struct
import sys
import time
import wave
import numpy as np
import pyaudio

def list_devices(p):
    print("\n--- 🎧 DAFTAR PERANGKAT AUDIO TERDETEKSI ---")
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    
    inputs = []
    outputs = []
    
    for i in range(numdevices):
        dev = p.get_device_info_by_host_api_device_index(0, i)
        max_in = dev.get('maxInputChannels')
        max_out = dev.get('maxOutputChannels')
        name = dev.get('name')
        
        if max_in > 0:
            inputs.append((i, name, max_in))
        if max_out > 0:
            outputs.append((i, name, max_out))
            
    print("\n[Mikrofon / Input Devices]:")
    for idx, name, ch in inputs:
        print(f"  Index [{idx}]: {name} (Channels: {ch})")
        
    print("\n[Speaker / Output Devices]:")
    for idx, name, ch in outputs:
        print(f"  Index [{idx}]: {name} (Channels: {ch})")
    print("-" * 50)
    return inputs, outputs

def live_meter(p, input_idx=None, duration=5):
    print(f"\n📊 UJI LEVEL SUARA MIKROFON (Live VU-Meter selama {duration} detik)...")
    print("Silakan bicara atau diam untuk melihat level suara:")
    print("Nilai saat diam = Ambience Noise | Nilai saat bicara = Voice Level\n")
    
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=input_idx,
        frames_per_buffer=1280
    )
    
    start_time = time.time()
    try:
        while time.time() - start_time < duration:
            data = stream.read(1280, exception_on_overflow=False)
            arr = np.frombuffer(data, dtype=np.int16)
            rms = np.sqrt(np.mean(arr.astype(np.float32) ** 2))
            
            bars = int(min(50, rms / 50))
            meter = "█" * bars + "-" * (50 - bars)
            sys.stdout.write(f"\rLevel: [{meter}] RMS: {rms:6.1f}")
            sys.stdout.flush()
            time.sleep(0.05)
    finally:
        stream.stop_stream()
        stream.close()
        print("\n")

def test_record_playback(p, input_idx=None, output_idx=None):
    print("\n🎙️ Merekam 3 detik suara Anda...")
    stream_in = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=input_idx,
        frames_per_buffer=1280
    )
    
    frames = []
    for _ in range(0, int(16000 / 1280 * 3)):
        data = stream_in.read(1280, exception_on_overflow=False)
        frames.append(data)
        
    stream_in.stop_stream()
    stream_in.close()
    print("✅ Perekaman selesai.")
    
    print("🔊 Memutar kembali hasil rekaman ke speaker...")
    stream_out = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        output=True,
        output_device_index=output_idx
    )
    
    for f in frames:
        stream_out.write(f)
        
    stream_out.stop_stream()
    stream_out.close()
    print("✅ Pengujian audio selesai!")

def main():
    p = pyaudio.PyAudio()
    try:
        list_devices(p)
        live_meter(p, duration=6)
        test_record_playback(p)
    finally:
        p.terminate()

if __name__ == "__main__":
    main()
