import json
import os
import re
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

# Jenis perangkat yang bisa ditambahkan dari Web UI / APK beserta nilai awalnya
DEVICE_TYPES = {
    "light": {"state": "off", "brightness": 100, "icon": "fa-lightbulb"},
    "switch": {"state": "off", "icon": "fa-plug"},
    "fan": {"state": "off", "speed": 2, "icon": "fa-fan"},
    "ac": {"state": "off", "temperature": 24, "mode": "cool", "icon": "fa-snowflake"},
}
TUYA_ID_RE = re.compile(r"^[A-Za-z0-9]{8,40}$")


def _clean_text(value, field: str, max_len: int, required: bool = True) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ValueError(f"{field} wajib diisi")
    if len(text) > max_len:
        raise ValueError(f"{field} maksimal {max_len} karakter")
    return text


def _clean_aliases(value, name: str) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    aliases = []
    for a in value or []:
        a = re.sub(r"\s+", " ", str(a)).strip().lower()
        if a and len(a) <= 40 and a not in aliases:
            aliases.append(a)
    if name.lower() not in aliases:
        aliases.insert(0, name.lower())
    return aliases[:10]


class SmartHomeRealtimeDB:
    def __init__(self, db_path="data/smart_home_db.json", web_server=None, firebase_bridge=None, tuya_bridge=None):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(self.base_dir, db_path)
        self.web_server = web_server
        self.firebase_bridge = firebase_bridge
        self.tuya_bridge = tuya_bridge
        self.lock = threading.Lock()
        self.devices: Dict[str, Dict[str, Any]] = {}
        self._load_db()

    def set_web_server(self, web_server):
        self.web_server = web_server
        self._broadcast_all()

    def set_firebase_bridge(self, firebase_bridge):
        self.firebase_bridge = firebase_bridge

    def set_tuya_bridge(self, tuya_bridge):
        self.tuya_bridge = tuya_bridge
        if not tuya_bridge:
            return
        # Migrasi sekali: pindahkan mapping tuya.devices di config.yaml ke database perangkat
        with self.lock:
            changed = False
            for dev_id, device in self.devices.items():
                if "tuya_id" not in device:
                    device["tuya_id"] = tuya_bridge.device_mappings.get(dev_id, "")
                    changed = True
            if changed:
                self._save_db_unlocked()
        if hasattr(tuya_bridge, "start_sync"):
            tuya_bridge.start_sync(self)

    def has_viewers(self) -> bool:
        return bool(self.web_server and getattr(self.web_server, "websockets", None))

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
            if "semua lampu" in q or q in ["lampu", "lampunya", "lampu rumah", "semua lampu"]:
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

    def update_device_state(self, device_id: str, updates: Dict[str, Any], sync_to_cloud=True, push_to_device=True) -> Optional[Dict[str, Any]]:
        with self.lock:
            if device_id in self.devices:
                device = self.devices[device_id]
                
                # Cek apakah ada perubahan nyata (Deduplikasi untuk mencegah loop/echo)
                has_changed = False
                for k, v in updates.items():
                    if k in ["state", "brightness", "is_open", "motion_detected", "temperature", "speed", "status"] and device.get(k) != v:
                        has_changed = True
                        break
                        
                if not has_changed and "state" in updates:
                    # Tidak ada perubahan status, abaikan pengiriman ulang ke Tuya/Cloud
                    return device

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

                # Kontrol perangkat fisik via Tuya Cloud Bridge jika aktif
                if push_to_device and self.tuya_bridge:
                    self.tuya_bridge.push_device_update(device_id, updates, tuya_id=device.get("tuya_id"))
                    
                return device
        return None

    # ---------- CRUD perangkat (Web UI / APK) ----------
    def _unique_id(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "perangkat"
        dev_id, n = base, 2
        while dev_id in self.devices:
            dev_id, n = f"{base}_{n}", n + 1
        return dev_id

    def _validate_tuya_id(self, tuya_id, exclude_id: Optional[str] = None) -> str:
        tuya_id = str(tuya_id or "").strip()
        if not tuya_id:
            return ""
        if not TUYA_ID_RE.match(tuya_id):
            raise ValueError("ID Tuya tidak valid")
        for other in self.devices.values():
            if other["id"] != exclude_id and other.get("tuya_id") == tuya_id:
                raise ValueError(f"Perangkat Tuya ini sudah dipakai oleh '{other.get('name')}'")
        return tuya_id

    def add_device(self, data: Dict[str, Any]) -> Dict[str, Any]:
        dev_type = str(data.get("type", "light"))
        if dev_type not in DEVICE_TYPES:
            raise ValueError("Jenis perangkat tidak dikenal")
        name = _clean_text(data.get("name"), "Nama", 40)
        room = _clean_text(data.get("room"), "Ruangan", 30)
        with self.lock:
            device = {
                "id": self._unique_id(name),
                "name": name,
                "room": room,
                "type": dev_type,
                "aliases": _clean_aliases(data.get("aliases"), name),
                "tuya_id": self._validate_tuya_id(data.get("tuya_id")),
                **DEVICE_TYPES[dev_type],
                "last_updated": time.time(),
            }
            self.devices[device["id"]] = device
            self._save_db_unlocked()
        self._after_structure_change()
        print(f"[SmartHomeDB] ➕ Perangkat ditambahkan: {device['id']} ({name}, {room})")
        return device

    def update_device_info(self, device_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.lock:
            device = self.devices.get(device_id)
            if not device:
                return None
            updated = dict(device)
            if "type" in data and data["type"] != updated.get("type"):
                if data["type"] not in DEVICE_TYPES:
                    raise ValueError("Jenis perangkat tidak dikenal")
                for k, v in DEVICE_TYPES[data["type"]].items():
                    updated.setdefault(k, v)
                updated["type"] = data["type"]
                updated["icon"] = DEVICE_TYPES[data["type"]]["icon"]
            if "name" in data:
                updated["name"] = _clean_text(data["name"], "Nama", 40)
            if "room" in data:
                updated["room"] = _clean_text(data["room"], "Ruangan", 30)
            if "aliases" in data or "name" in data:
                aliases = data.get("aliases")
                if aliases is None:
                    # Nama lama tidak lagi dipakai sebagai nama panggilan setelah perangkat diganti nama
                    old_name = str(device.get("name", "")).lower()
                    aliases = [a for a in updated.get("aliases", []) if a != old_name]
                updated["aliases"] = _clean_aliases(aliases, updated["name"])
            if "tuya_id" in data:
                updated["tuya_id"] = self._validate_tuya_id(data["tuya_id"], exclude_id=device_id)
                if updated["tuya_id"] != device.get("tuya_id"):
                    updated.pop("online", None)
            updated["last_updated"] = time.time()
            device.clear()
            device.update(updated)
            self._save_db_unlocked()
        self._after_structure_change()
        return device

    def delete_device(self, device_id: str) -> bool:
        with self.lock:
            if device_id not in self.devices:
                return False
            removed = self.devices.pop(device_id)
            self._save_db_unlocked()
        self._after_structure_change()
        print(f"[SmartHomeDB] 🗑️ Perangkat dihapus: {device_id} ({removed.get('name')})")
        return True

    def set_device_report(self, device_id: str, fields: Dict[str, Any]) -> None:
        """Status yang dilaporkan perangkat fisik (online, nyala/mati dari Smart Life / saklar dinding).
        Tidak dikirim balik ke perangkat agar tidak terjadi loop."""
        with self.lock:
            device = self.devices.get(device_id)
            if not device:
                return
            changes = {k: v for k, v in fields.items() if device.get(k) != v}
            if not changes:
                return
            if set(changes) == {"online"}:
                device.update(changes)
                self._save_db_unlocked()
                self._broadcast_device(device)
                return
        self.update_device_state(device_id, changes, sync_to_cloud=True, push_to_device=False)

    def _after_structure_change(self):
        self._broadcast_all()
        if self.firebase_bridge and hasattr(self.firebase_bridge, "push_all_devices"):
            self.firebase_bridge.push_all_devices()
