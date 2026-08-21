import json
import os
import threading
import time
from typing import Dict, Any, List, Optional

DEFAULT_DEVICES = {
    "lampu_ruang_tamu": {
        "id": "lampu_ruang_tamu",
        "name": "Lampu Ruang Tamu",
        "aliases": ["lampu ruang tamu", "lampu tengah", "lampu utama", "lampu tamu"],
        "type": "light",
        "room": "Ruang Tamu",
        "state": "off",
        "brightness": 100,
        "icon": "fa-lightbulb",
        "last_updated": time.time()
    },
    "lampu_kamar": {
        "id": "lampu_kamar",
        "name": "Lampu Kamar",
        "aliases": ["lampu kamar", "lampu tidur", "lampu kamar tidur"],
        "type": "light",
        "room": "Kamar Tidur",
        "state": "off",
        "brightness": 80,
        "icon": "fa-lightbulb",
        "last_updated": time.time()
    },
    "lampu_teras": {
        "id": "lampu_teras",
        "name": "Lampu Teras",
        "aliases": ["lampu teras", "lampu luar", "lampu depan"],
        "type": "light",
        "room": "Teras",
        "state": "off",
        "brightness": 100,
        "icon": "fa-lightbulb",
        "last_updated": time.time()
    },
    "lampu_dapur": {
        "id": "lampu_dapur",
        "name": "Lampu Dapur",
        "aliases": ["lampu dapur", "lampu meja makan"],
        "type": "light",
        "room": "Dapur",
        "state": "off",
        "brightness": 100,
        "icon": "fa-lightbulb",
        "last_updated": time.time()
    },
    "ac_kamar": {
        "id": "ac_kamar",
        "name": "AC Kamar",
        "aliases": ["ac", "ac kamar", "pendingin ruangan", "ac kamar tidur"],
        "type": "ac",
        "room": "Kamar Tidur",
        "state": "off",
        "temperature": 24,
        "mode": "cool",
        "icon": "fa-snowflake",
        "last_updated": time.time()
    },
    "kipas_angin": {
        "id": "kipas_angin",
        "name": "Kipas Angin",
        "aliases": ["kipas", "kipas angin", "kipas ruang tamu"],
        "type": "fan",
        "room": "Ruang Tamu",
        "state": "off",
        "speed": 2,
        "icon": "fa-fan",
        "last_updated": time.time()
    },
    "sensor_suhu_ruang": {
        "id": "sensor_suhu_ruang",
        "name": "Sensor Suhu & Kelembaban",
        "aliases": ["sensor suhu", "suhu ruangan", "kelembaban", "sensor dht", "suhu"],
        "type": "sensor_climate",
        "room": "Ruang Tamu",
        "temperature": 27.2,
        "humidity": 68,
        "icon": "fa-temperature-half",
        "last_updated": time.time()
    },
    "sensor_pintu_utama": {
        "id": "sensor_pintu_utama",
        "name": "Sensor Pintu Depan",
        "aliases": ["sensor pintu", "pintu depan", "pintu utama", "pintu rumah", "pintu"],
        "type": "sensor_contact",
        "room": "Pintu Depan",
        "is_open": False,
        "icon": "fa-door-closed",
        "last_updated": time.time()
    },
    "sensor_gerak_teras": {
        "id": "sensor_gerak_teras",
        "name": "Sensor Gerak Teras",
        "aliases": ["sensor gerak", "gerakan teras", "sensor pir"],
        "type": "sensor_motion",
        "room": "Teras",
        "motion_detected": False,
        "icon": "fa-person-walking",
        "last_updated": time.time()
    },
    "sensor_gas_dapur": {
        "id": "sensor_gas_dapur",
        "name": "Sensor Gas & Asap",
        "aliases": ["sensor gas", "kebocoran gas", "asap dapur", "sensor mq2"],
        "type": "sensor_gas",
        "room": "Dapur",
        "status": "Aman",
        "ppm": 24,
        "icon": "fa-fire-flame-simple",
        "last_updated": time.time()
    }
}

class SmartHomeRealtimeDB:
    def __init__(self, db_path="data/smart_home_db.json", web_server=None, firebase_bridge=None):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(self.base_dir, db_path)
        self.web_server = web_server
        self.firebase_bridge = firebase_bridge
        self.lock = threading.Lock()
        self.devices: Dict[str, Dict[str, Any]] = {}
        self._load_db()

    def set_web_server(self, web_server):
        self.web_server = web_server
        self._broadcast_all()

    def set_firebase_bridge(self, firebase_bridge):
        self.firebase_bridge = firebase_bridge

    def _load_db(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            if os.path.exists(self.db_path):
                try:
                    with open(self.db_path, "r") as f:
                        self.devices = json.load(f)
                except Exception as e:
                    print(f"[SmartHomeDB Warning] Gagal membaca DB, memuat default: {e}")
                    self.devices = DEFAULT_DEVICES.copy()
            else:
                self.devices = DEFAULT_DEVICES.copy()
                self._save_db_unlocked()

    def _save_db_unlocked(self):
        try:
            with open(self.db_path, "w") as f:
                json.dump(self.devices, f, indent=2)
        except Exception as e:
            print(f"[SmartHomeDB Error] Gagal menyimpan DB: {e}")

    def _broadcast_all(self):
        if self.web_server:
            self.web_server.broadcast("smart_home_sync", {
                "devices": list(self.devices.values())
            })

    def _broadcast_device(self, device: Dict[str, Any]):
        if self.web_server:
            self.web_server.broadcast("smart_home_device_update", {
                "device": device
            })

    def get_all_devices(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.devices.values())

    def get_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.devices.get(device_id)

    def find_device(self, query: str) -> List[Dict[str, Any]]:
        q = query.lower().strip()
        matched = []
        with self.lock:
            if "semua lampu" in q or (q == "lampu"):
                return [d for d in self.devices.values() if d.get("type") == "light"]
            
            if "semua" in q or "semua perangkat" in q:
                return list(self.devices.values())

            for d in self.devices.values():
                d_id = d.get("id", "").lower()
                d_name = d.get("name", "").lower()
                aliases = [a.lower() for a in d.get("aliases", [])]
                
                if q in d_id or q in d_name or any(q in a or a in q for a in aliases):
                    matched.append(d)
        return matched

    def update_device_state(self, device_id: str, updates: Dict[str, Any], sync_to_cloud=True) -> Optional[Dict[str, Any]]:
        with self.lock:
            if device_id in self.devices:
                device = self.devices[device_id]
                device.update(updates)
                device["last_updated"] = time.time()
                
                if device.get("type") == "light":
                    device["icon"] = "fa-lightbulb"
                elif device.get("type") == "sensor_contact":
                    device["icon"] = "fa-door-open" if device.get("is_open") else "fa-door-closed"
                elif device.get("type") == "sensor_motion":
                    device["icon"] = "fa-person-running" if device.get("motion_detected") else "fa-person-walking"
                elif device.get("type") == "sensor_gas":
                    ppm = device.get("ppm", 0)
                    device["status"] = "Waspada! Gas Terdeteksi" if ppm > 100 else "Aman"
                    
                self._save_db_unlocked()
                self._broadcast_device(device)
                
                # Sinkronkan ke Firebase Cloud jika aktif
                if sync_to_cloud and self.firebase_bridge:
                    self.firebase_bridge.push_device_update(device_id, updates)
                    
                return device
        return None
