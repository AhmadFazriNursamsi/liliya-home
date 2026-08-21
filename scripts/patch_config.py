#!/usr/bin/env python3
"""
Script untuk memperbaiki konfigurasi audio STB secara otomatis.
- Mendeteksi nomor kartu suara USB Mic yang benar
- Membuat /etc/asound.conf agar PyAudio bisa menggunakan 16000 Hz via plughw
- Mengupdate config.yaml
"""
import os
import subprocess
import yaml

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

card = find_usb_audio_card()

# 1. Buat /etc/asound.conf agar PyAudio bisa gunakan 16000Hz via plughw
asound_conf = f"""pcm.!default {{
    type plug
    slave {{
        pcm "hw:{card},0"
        rate 16000
        channels 1
    }}
}}
ctl.!default {{
    type hw
    card {card}
}}
"""

try:
    with open('/etc/asound.conf', 'w') as f:
        f.write(asound_conf)
    print(f"[ALSA] /etc/asound.conf dibuat untuk card {card} dengan rate 16000 Hz")
except PermissionError:
    print("[ALSA] ERROR: Jalankan script ini dengan sudo!")
    exit(1)

# 2. Update config.yaml
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(base_dir, 'config.yaml')

with open(config_path) as f:
    cfg = yaml.safe_load(f)

cfg['audio']['input_device_index'] = card
cfg['audio']['output_device_index'] = card
cfg['audio']['sample_rate'] = 16000

with open(config_path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

print(f"[Config] config.yaml diupdate: card={card}, sample_rate=16000")
print("")
print("=" * 60)
print("Selesai! Jalankan:")
print("  sudo systemctl restart stb-assistant.service")
print("=" * 60)
