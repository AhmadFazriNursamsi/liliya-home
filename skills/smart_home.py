from typing import Optional
from core.smart_home_db import SmartHomeRealtimeDB

class SmartHomeSkill:
    def __init__(self, db: SmartHomeRealtimeDB):
        self.db = db

    def control_smart_home_device(self, device_name: str, action: str, value: Optional[str] = None) -> str:
        """
        Mengontrol perangkat smart home seperti lampu, AC, kipas angin, atau saklar.
        
        Args:
            device_name: Nama perangkat yang ingin dikontrol (contoh: 'lampu ruang tamu', 'lampu kamar', 'semua lampu', 'ac kamar', 'kipas angin').
            action: Aksi yang dilakukan: 'turn_on' (nyalakan), 'turn_off' (matikan), 'set_brightness' (atur kecerahan), 'set_temperature' (atur suhu AC), 'set_speed' (atur kecepatan kipas).
            value: Nilai opsional jika ada (contoh: '50' untuk kecerahan 50%, '22' untuk suhu AC 22°C, '3' untuk kecepatan kipas).
        """
        matched_devices = self.db.find_device(device_name)
        if not matched_devices:
            return f"Maaf, perangkat '{device_name}' tidak ditemukan di sistem smart home."

        action_clean = action.lower().strip()
        results = []

        for dev in matched_devices:
            dev_id = dev["id"]
            dev_name = dev["name"]
            dev_type = dev.get("type")

            if action_clean in ["turn_on", "nyalakan", "on", "buka"]:
                self.db.update_device_state(dev_id, {"state": "on"})
                results.append(f"{dev_name} dinyalakan")
                
            elif action_clean in ["turn_off", "matikan", "off", "tutup"]:
                self.db.update_device_state(dev_id, {"state": "off"})
                results.append(f"{dev_name} dimatikan")
                
            elif action_clean in ["set_brightness", "kecerahan", "redupkan", "terangkan"]:
                try:
                    val_num = int("".join(filter(str.isdigit, str(value)))) if value else 50
                    val_num = max(0, min(100, val_num))
                    self.db.update_device_state(dev_id, {"state": "on", "brightness": val_num})
                    results.append(f"kecerahan {dev_name} diatur ke {val_num}%")
                except Exception:
                    self.db.update_device_state(dev_id, {"state": "on", "brightness": 50})
                    results.append(f"kecerahan {dev_name} diatur ke 50%")
                    
            elif action_clean in ["set_temperature", "suhu_ac", "atur_suhu"]:
                try:
                    temp_num = int("".join(filter(str.isdigit, str(value)))) if value else 24
                    temp_num = max(16, min(30, temp_num))
                    self.db.update_device_state(dev_id, {"state": "on", "temperature": temp_num})
                    results.append(f"suhu {dev_name} diatur ke {temp_num} derajat Celsius")
                except Exception:
                    self.db.update_device_state(dev_id, {"state": "on", "temperature": 24})
                    results.append(f"suhu {dev_name} diatur ke 24 derajat Celsius")
                    
            elif action_clean in ["set_speed", "kecepatan"]:
                try:
                    spd_num = int("".join(filter(str.isdigit, str(value)))) if value else 2
                    spd_num = max(1, min(3, spd_num))
                    self.db.update_device_state(dev_id, {"state": "on", "speed": spd_num})
                    results.append(f"kecepatan {dev_name} diatur ke level {spd_num}")
                except Exception:
                    self.db.update_device_state(dev_id, {"state": "on", "speed": 2})
                    results.append(f"kecepatan {dev_name} diatur ke level 2")
            else:
                results.append(f"perintah pada {dev_name} telah diproses")

        if len(results) == 1:
            return f"Baik, {results[0]}."
        elif len(results) > 1:
            return f"Baik, {', '.join(results)}."
        return "Perintah telah dijalankan pada perangkat smart home."

    def get_smart_home_status(self, query: str = "semua") -> str:
        """
        Mengecek status perangkat dan sensor smart home secara realtime.
        
        Args:
            query: Kategori atau nama perangkat yang ingin dicek ('semua', 'lampu', 'sensor', 'suhu', 'pintu', 'gas', 'ac').
        """
        q = query.lower().strip()
        devices = self.db.get_all_devices()
        
        if not devices:
            return "Belum ada perangkat yang terdaftar di database smart home."

        # Cek kategori lampu
        if q in ["lampu", "lampu-lampu"]:
            lights = [d for d in devices if d.get("type") == "light"]
            on_lights = [d["name"] for d in lights if d.get("state") == "on"]
            off_lights = [d["name"] for d in lights if d.get("state") == "off"]
            
            if not on_lights:
                return "Semua lampu saat ini dalam keadaan mati."
            elif not off_lights:
                return "Semua lampu saat ini sedang menyala."
            else:
                return f"Lampu yang menyala yaitu {', '.join(on_lights)}, sedangkan {', '.join(off_lights)} dalam keadaan mati."

        # Cek kategori sensor & iklim
        if q in ["sensor", "suhu", "kelembaban", "pintu", "keamanan", "gas"]:
            sensor_reports = []
            for d in devices:
                t = d.get("type")
                if t == "sensor_climate":
                    sensor_reports.append(f"Suhu {d['room']} saat ini {d.get('temperature')}°C dengan kelembapan {d.get('humidity')}%")
                elif t == "sensor_contact":
                    status = "terbuka" if d.get("is_open") else "tertutup rapat"
                    sensor_reports.append(f"{d['name']} dalam kondisi {status}")
                elif t == "sensor_gas":
                    sensor_reports.append(f"{d['name']} berstatus {d.get('status', 'Aman')} (kadar {d.get('ppm', 0)} ppm)")
                elif t == "sensor_motion":
                    motion = "terdeteksi gerakan" if d.get("motion_detected") else "tidak ada pergerakan"
                    sensor_reports.append(f"{d['name']} {motion}")
                    
            return "Status sensor saat ini: " + ". ".join(sensor_reports) + "."

        # Cek spesifik perangkat tunggal
        matched = self.db.find_device(q)
        if matched and len(matched) == 1:
            d = matched[0]
            t = d.get("type")
            if t == "light":
                st = "menyala" if d.get("state") == "on" else "mati"
                return f"{d['name']} saat ini sedang {st} dengan kecerahan {d.get('brightness', 100)}%."
            elif t == "ac":
                st = "menyala" if d.get("state") == "on" else "mati"
                return f"{d['name']} sedang {st} pada suhu {d.get('temperature', 24)}°C."
            elif t == "fan":
                st = "menyala" if d.get("state") == "on" else "mati"
                return f"{d['name']} sedang {st} dengan kecepatan level {d.get('speed', 2)}."
            elif t == "sensor_climate":
                return f"{d['name']} menunjukkan suhu {d.get('temperature')}°C dan kelembapan {d.get('humidity')}%."
            elif t == "sensor_contact":
                st = "terbuka" if d.get("is_open") else "tertutup rapat"
                return f"{d['name']} dalam kondisi {st}."
            elif t == "sensor_gas":
                return f"{d['name']} berstatus {d.get('status', 'Aman')} ({d.get('ppm', 0)} ppm)."

        # Laporan lengkap seluruh rumah
        lights_on = [d["name"] for d in devices if d.get("type") == "light" and d.get("state") == "on"]
        climate = next((d for d in devices if d.get("type") == "sensor_climate"), None)
        door = next((d for d in devices if d.get("type") == "sensor_contact"), None)
        gas = next((d for d in devices if d.get("type") == "sensor_gas"), None)
        
        summary_parts = []
        if lights_on:
            summary_parts.append(f"Lampu yang menyala: {', '.join(lights_on)}")
        else:
            summary_parts.append("Semua lampu mati")
            
        if climate:
            summary_parts.append(f"Suhu ruangan {climate.get('temperature')}°C ({climate.get('humidity')}% kelembapan)")
            
        if door:
            door_st = "terbuka" if door.get("is_open") else "tertutup aman"
            summary_parts.append(f"Pintu depan {door_st}")
            
        if gas:
            summary_parts.append(f"Sensor gas {gas.get('status', 'Aman')}")
            
        return "Laporan Smart Home: " + "; ".join(summary_parts) + "."
