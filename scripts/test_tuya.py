import os
import sys
import yaml

# Tambahkan path project ke sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import tinytuya

def test_tuya_connection():
    config_path = os.path.join(BASE_DIR, "config.yaml")
    if not os.path.exists(config_path):
        print(f"File config tidak ditemukan di {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    tuya_cfg = config.get("tuya", {})
    region = tuya_cfg.get("api_region", "us")
    api_key = tuya_cfg.get("api_key", "").strip()
    api_secret = tuya_cfg.get("api_secret", "").strip()
    devices = tuya_cfg.get("devices", {})

    print("=" * 60)
    print("🧪 UJI KONEKSI TUYA CLOUD BRIDGE (BARDI / HISTAR)")
    print("=" * 60)
    print(f"Region     : {region}")
    print(f"Access ID  : {api_key[:6]}..." if api_key else "Access ID  : (KOSONG)")
    print(f"Access Sec : {'*' * len(api_secret)}" if api_secret else "Access Sec : (KOSONG)")
    print(f"Devices    : {devices}")
    print("-" * 60)

    if not api_key or not api_secret:
        print("❌ Silakan isi 'api_key' (Access ID) dan 'api_secret' di config.yaml terlebih dahulu.")
        return

    print("📡 Menghubungkan ke Tuya Cloud...")
    try:
        cloud = tinytuya.Cloud(
            apiRegion=region,
            apiKey=api_key,
            apiSecret=api_secret
        )

        for dev_name, dev_id in devices.items():
            print(f"\n🔍 Mengecek perangkat: {dev_name} (ID: {dev_id})...")
            status = cloud.getstatus(dev_id)
            print(f"   Status Response: {status}")

            if status and status.get("success"):
                print(f"   ✅ Perangkat ditemukan dan terhubung!")
            else:
                print(f"   ⚠️ Respon error/warning: {status.get('msg', 'Unknown error')}")

        print("\n" + "=" * 60)
        print("Selesai pengujian.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_tuya_connection()
