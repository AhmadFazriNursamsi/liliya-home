#!/usr/bin/env python3
"""
Script untuk memperbaiki konfigurasi audio STB secara otomatis.
Mendeteksi nomor kartu suara USB Mic yang benar dan mengupdate config.yaml.
"""
import os
import yaml
import subprocess

def find_usb_audio_card():
    try:
        result = subprocess.run(['arecord', '-l'], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if 'USB Audio' in line or 'AB13X' in line or 'USB' in line.upper():
                card_num = int(line.split()[1].replace(':', ''))
                print(f"[AutoDetect] USB Mic ditemukan di card {card_num}")
                return card_num
    except Exception as e:
        print(f"[AutoDetect] Gagal mendeteksi: {e}")
    return 0

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(base_dir, 'config.yaml')

with open(config_path) as f:
    cfg = yaml.safe_load(f)

card = find_usb_audio_card()
cfg['audio']['input_device_index'] = card
cfg['audio']['output_device_index'] = card

with open(config_path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

print(f"Selesai! input_device_index = {card}, output_device_index = {card}")
print("Jalankan: sudo systemctl restart stb-assistant.service")
