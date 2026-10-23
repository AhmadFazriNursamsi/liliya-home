import os
import threading
import time
import logging
from typing import Dict, Any, List, Optional

try:
    import tinytuya
except ImportError:
    tinytuya = None

# Kategori Tuya -> jenis perangkat BUDI (untuk saran saat menambah perangkat)
CATEGORY_TYPES = {
    "dj": "light", "dd": "light", "xdd": "light", "fwd": "light", "dc": "light", "tgq": "light", "tgkg": "light",
    "kg": "switch", "cz": "switch", "pc": "switch", "tdq": "switch", "cl": "switch",
    "fs": "fan", "kt": "ac", "ktkzq": "ac",
}
SWITCH_CODES = ("switch_led", "switch_1", "switch", "switch_led_1")


class TuyaCloudBridge:
    def __init__(self, config: dict):
        self.config = config.get("tuya", {})
        self.enabled = self.config.get("enabled", False)
        self.api_region = self.config.get("api_region", "us").lower().strip() # us, eu, cn, in
        self.api_key = self.config.get("api_key", "").strip()
        self.api_secret = self.config.get("api_secret", "").strip()
        self.device_mappings = self.config.get("devices", {}) # {"lampu_ruang_tamu": "a367dbdwalw1wp1n", ...}
        
        self.cloud = None
        self._device_code_cache: Dict[str, str] = {} # tuya_dev_id -> switch code (e.g. "switch_led")
        self._last_sent_states: Dict[str, tuple] = {} # tuya_dev_id -> (is_on, timestamp)
        self._last_command_at: Dict[str, float] = {}  # tuya_dev_id -> waktu perintah terakhir dari BUDI
        self.sync_active_interval = int(self.config.get("sync_interval_active", 15))
        self.sync_idle_interval = int(self.config.get("sync_interval_idle", 300))
        self._sync_wakeup = threading.Event()
        self._offline_since: Dict[str, float] = {}  # tuya_dev_id -> pertama kali dilaporkan offline
        self.offline_grace = int(self.config.get("offline_grace_seconds", 60))
        self._sync_thread = None
        
        if self.enabled and self.api_key and self.api_secret and tinytuya:
            try:
                self.cloud = tinytuya.Cloud(
                    apiRegion=self.api_region,
                    apiKey=self.api_key,
                    apiSecret=self.api_secret
                )
                print(f"[TuyaBridge] 🌐 Tuya Cloud Bridge aktif (Region: {self.api_region.upper()}) dengan {len(self.device_mappings)} mapping perangkat.")
                self._detect_device_functions_async()
            except Exception as e:
                print(f"[TuyaBridge Error] Gagal inisialisasi Tuya Cloud: {e}")
        elif self.enabled and (not self.api_key or not self.api_secret):
            print("[TuyaBridge Warning] Fitur Tuya diaktifkan tapi api_key / api_secret belum diisi di config.yaml.")

    def _detect_device_functions_async(self):
        """Deteksi kode DP switch & brightness secara otomatis di latar belakang."""
        def _detect():
            if not self.cloud:
                return
            for name, dev_id in self.device_mappings.items():
                try:
                    status_res = self.cloud.getstatus(dev_id)
                    if status_res and "result" in status_res and isinstance(status_res["result"], list):
                        codes = [item.get("code") for item in status_res["result"] if isinstance(item, dict)]
                        # Cari switch code
                        for candidate in ["switch_led", "switch_1", "switch", "switch_led_1"]:
                            if candidate in codes:
                                self._device_code_cache[dev_id] = candidate
                                break
                        print(f"[TuyaBridge] 🔍 Terdeteksi perangkat '{name}' ({dev_id}) mendukung DP: {codes}")
                except Exception as e:
                    print(f"[TuyaBridge] Gagal deteksi DP perangkat '{name}': {e}")
        threading.Thread(target=_detect, daemon=True).start()

    def push_device_update(self, device_id: str, updates: dict, tuya_id: Optional[str] = None):
        """Mengirim perintah perubahan state / brightness ke Tuya Cloud secara asynchronous."""
        if not self.enabled or not self.cloud:
            return

        # ID Tuya disimpan di database perangkat; mapping config.yaml hanya cadangan untuk data lama
        tuya_dev_id = tuya_id if tuya_id is not None else self.device_mappings.get(device_id)
        if not tuya_dev_id:
            # Tidak ada mapping untuk device_id ini, lewati
            return

        # Anti-Flicker & Debounce: Jika status yang diminta sama dengan yang baru saja dikirim (< 5 detik), abaikan
        if "state" in updates and "brightness" not in updates:
            is_on = (str(updates["state"]).lower() in ["on", "true", "1", "nyalakan", "buka"])
            last_state, last_time = self._last_sent_states.get(tuya_dev_id, (None, 0))
            if last_state == is_on and (time.time() - last_time) < 5.0:
                return
            self._last_sent_states[tuya_dev_id] = (is_on, time.time())

        self._last_command_at[tuya_dev_id] = time.time()

        def _worker():
            try:
                commands = []
                
                # Cari kode switch yang cocok
                switch_code = self._device_code_cache.get(tuya_dev_id, "switch_led")
                
                # Handler State ON/OFF
                if "state" in updates:
                    is_on = (str(updates["state"]).lower() in ["on", "true", "1", "nyalakan", "buka"])
                    commands.append({"code": switch_code, "value": is_on})
                
                # Handler Brightness (1-100% -> 10-1000)
                if "brightness" in updates and updates.get("state") != "off":
                    try:
                        val_pct = int(updates["brightness"])
                        val_pct = max(1, min(100, val_pct))
                        bright_val = int(val_pct * 10)
                        commands.append({"code": "bright_value_v2", "value": bright_val})
                    except Exception:
                        pass

                if not commands:
                    return

                payload = {"commands": commands}
                print(f"[TuyaBridge] 📡 Mengirim perintah ke Tuya Dev ID '{tuya_dev_id}': {payload}")
                res = self.cloud.sendcommand(tuya_dev_id, payload)
                
                # Jika gagal karena kode DP tidak cocok, coba fallback ke switch_1 / switch
                if res and not res.get("success"):
                    if res.get("code") in [1106, 2008, 1100] and switch_code == "switch_led":
                        fallback_code = "switch_1"
                        commands = [{"code": fallback_code, "value": (str(updates.get("state", "on")).lower() in ["on", "true", "1"])}]
                        res_fallback = self.cloud.sendcommand(tuya_dev_id, {"commands": commands})
                        if res_fallback and res_fallback.get("success"):
                            self._device_code_cache[tuya_dev_id] = fallback_code
                            print(f"[TuyaBridge] ✅ Berhasil kontrol lampu fisik '{device_id}' via fallback ({fallback_code})")
                            return

                if res and res.get("success"):
                    print(f"[TuyaBridge] ✅ Berhasil kontrol lampu fisik '{device_id}' ({tuya_dev_id})")
                else:
                    print(f"[TuyaBridge Warning] Respon Tuya Cloud: {res}")
            except Exception as e:
                print(f"[TuyaBridge Error] Gagal kirim perintah ke Tuya: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    # ---------- Daftar perangkat akun Smart Life (untuk fitur Tambah Perangkat) ----------
    def _online_map(self, tuya_ids: List[str]) -> Dict[str, dict]:
        """Info perangkat (termasuk online) untuk banyak ID sekaligus, 20 ID per panggilan API."""
        info = {}
        for i in range(0, len(tuya_ids), 20):
            chunk = tuya_ids[i:i + 20]
            res = self.cloud.cloudrequest("/v1.0/devices", query={"device_ids": ",".join(chunk)})
            if res and res.get("success"):
                for d in (res.get("result") or {}).get("devices", []):
                    info[d.get("id")] = d
        return info

    def list_cloud_devices(self) -> List[dict]:
        """Semua perangkat di akun Tuya / Smart Life, tanpa gateway."""
        if not self.enabled or not self.cloud:
            raise RuntimeError("Tuya belum aktif di config.yaml")
        devices = self.cloud.getdevices(verbose=False)
        if not isinstance(devices, list):
            raise RuntimeError(f"Gagal mengambil daftar perangkat Tuya: {devices}")
        info = self._online_map([d["id"] for d in devices if d.get("id")])
        result = []
        for d in devices:
            category = d.get("category", "")
            if category.startswith("wg"):
                continue  # gateway, tidak bisa dinyalakan/dimatikan
            result.append({
                "tuya_id": d.get("id"),
                "name": d.get("name") or d.get("product_name") or d.get("id"),
                "product_name": d.get("product_name", ""),
                "category": category,
                "suggested_type": CATEGORY_TYPES.get(category, "switch"),
                "online": bool(info.get(d.get("id"), {}).get("online")),
                "via_gateway": bool(d.get("gateway_id")),
            })
        result.sort(key=lambda x: (not x["online"], x["name"].lower()))
        return result

    # ---------- Sinkronisasi status dari Tuya ke BUDI ----------
    def _read_reports(self, tuya_ids: List[str]) -> Dict[str, dict]:
        """Online + nyala/mati + kecerahan yang dilaporkan Tuya. Field yang tidak dilaporkan tidak diisi."""
        reports = {tid: {} for tid in tuya_ids}
        for tid, d in self._online_map(tuya_ids).items():
            if tid in reports:
                reports[tid]["online"] = bool(d.get("online"))
        for i in range(0, len(tuya_ids), 20):
            chunk = tuya_ids[i:i + 20]
            res = self.cloud.cloudrequest("/v1.0/iot-03/devices/status", query={"device_ids": ",".join(chunk)})
            if not (res and res.get("success")):
                continue
            for item in res.get("result") or []:
                status = {s.get("code"): s.get("value") for s in item.get("status") or []}
                report = reports.get(item.get("id"))
                if report is None or not status:
                    continue
                for code in SWITCH_CODES:
                    if code in status:
                        report["state"] = "on" if status[code] else "off"
                        break
                if "bright_value_v2" in status:
                    report["brightness"] = max(1, min(100, round(int(status["bright_value_v2"]) / 10)))
        return reports

    def sync_now(self, db) -> None:
        devices = [d for d in db.get_all_devices() if d.get("tuya_id")]
        if not devices:
            return
        reports = self._read_reports([d["tuya_id"] for d in devices])
        now = time.time()
        for d in devices:
            report = reports.get(d["tuya_id"]) or {}
            # Abaikan status sesaat setelah BUDI mengirim perintah (cloud masih membawa status lama)
            if now - self._last_command_at.get(d["tuya_id"], 0) < 20:
                report.pop("state", None)
                report.pop("brightness", None)
            if report.get("state") == "off":
                report.pop("brightness", None)
            # Status online Tuya untuk perangkat Bluetooth lewat gateway sering naik-turun sesaat;
            # tandai offline hanya bila terus-menerus offline melewati masa tenggang.
            if report.get("online") is False:
                since = self._offline_since.setdefault(d["tuya_id"], now)
                if now - since < self.offline_grace:
                    report.pop("online")
            elif report.get("online") is True:
                self._offline_since.pop(d["tuya_id"], None)
            if report:
                db.set_device_report(d["id"], report)

    def start_sync(self, db) -> None:
        """Polling ringan: tiap 15 detik saat ada yang membuka aplikasi, tiap 5 menit saat tidak ada."""
        if not self.enabled or not self.cloud or self._sync_thread:
            return

        def _loop():
            time.sleep(5)
            while True:
                try:
                    self.sync_now(db)
                except Exception as e:
                    print(f"[TuyaBridge] Sinkronisasi status gagal: {e}")
                interval = self.sync_active_interval if db.has_viewers() else self.sync_idle_interval
                self._sync_wakeup.wait(interval)
                self._sync_wakeup.clear()

        self._sync_thread = threading.Thread(target=_loop, daemon=True)
        self._sync_thread.start()
        print(f"[TuyaBridge] 🔄 Sinkronisasi status aktif ({self.sync_active_interval} dtk saat aplikasi dibuka, {self.sync_idle_interval} dtk saat idle)")

    def request_sync(self) -> None:
        """Minta sinkronisasi segera (mis. saat aplikasi baru dibuka)."""
        self._sync_wakeup.set()
