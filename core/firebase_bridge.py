import json
import threading
import time
import requests

class FirebaseRealtimeBridge:
    def __init__(self, config, smart_home_db):
        self.config = config.get("firebase", {})
        self.enabled = self.config.get("enabled", False)
        self.database_url = self.config.get("database_url", "").rstrip("/")
        self.auth_secret = self.config.get("auth_secret", "")
        self.sync_path = self.config.get("sync_path", "smart_home/devices").strip("/")
        self.db = smart_home_db
        
        self.thread = None
        self.is_running = False
        
        if self.enabled and self.database_url and "your-project" not in self.database_url:
            self.start_sync()

    def _get_url(self, path=""):
        p = f"{self.sync_path}/{path}".strip("/")
        auth_param = f"?auth={self.auth_secret}" if self.auth_secret else ""
        return f"{self.database_url}/{p}.json{auth_param}"

    def push_device_update(self, device_id: str, updates: dict):
        """Mengirim pembaruan status dari STB (suara / klik layar) ke Firebase Cloud."""
        if not self.enabled or not self.database_url or "your-project" in self.database_url:
            return

        def _send():
            try:
                url = self._get_url(device_id)
                requests.patch(url, json=updates, timeout=4)
                print(f"[FirebaseCloud] ☁️ Berhasil sync '{device_id}' ke Firebase Cloud.")
            except Exception as e:
                print(f"[FirebaseCloud Error] Gagal kirim ke cloud: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def push_all_devices(self):
        """Inisialisasi awal: upload seluruh data perangkat lokal ke Firebase."""
        if not self.enabled or not self.database_url or "your-project" in self.database_url:
            return

        def _send_all():
            try:
                url = self._get_url()
                devices_map = {d["id"]: d for d in self.db.get_all_devices()}
                requests.put(url, json=devices_map, timeout=6)
                print(f"[FirebaseCloud] ☁️ Sinkronisasi awal {len(devices_map)} perangkat ke Firebase Cloud sukses!")
            except Exception as e:
                print(f"[FirebaseCloud Error] Inisialisasi awal gagal: {e}")

        threading.Thread(target=_send_all, daemon=True).start()

    def start_sync(self):
        """Mendengarkan event stream realtime dari Firebase (SSE / Server-Sent Events)."""
        if self.is_running:
            return
            
        self.is_running = True
        self.push_all_devices()

        def _listener():
            print(f"[FirebaseCloud] 🚀 Mendengarkan pembaruan cloud live dari: {self.database_url}")
            stream_url = self._get_url()
            
            while self.is_running:
                try:
                    headers = {"Accept": "text/event-stream"}
                    with requests.get(stream_url, headers=headers, stream=True, timeout=90) as resp:
                        if resp.status_code == 200:
                            for line in resp.iter_lines():
                                if not self.is_running:
                                    break
                                if not line:
                                    continue
                                line_str = line.decode('utf-8')
                                if line_str.startswith("data:"):
                                    data_json = line_str[5:].strip()
                                    if data_json and data_json != "null":
                                        self._handle_cloud_event(json.loads(data_json))
                        else:
                            time.sleep(5)
                except Exception as e:
                    time.sleep(4)

        self.thread = threading.Thread(target=_listener, daemon=True)
        self.thread.start()

    def _handle_cloud_event(self, event_data: dict):
        """Memproses data yang diubah dari HP / Kantor di Firebase dan menerapkan ke STB lokal."""
        try:
            path = event_data.get("path", "").strip("/")
            data = event_data.get("data")
            
            if data is None or path == "":
                return
                
            parts = path.split("/")
            device_id = parts[0]
            
            if len(parts) == 1:
                # Update objek dictionary device utuh (contoh path: "lampu_ruang_tamu", data: {"state": "on"})
                if isinstance(data, dict):
                    self.db.update_device_state(device_id, data, sync_to_cloud=False)
                    print(f"[FirebaseCloud] 📥 Kontrol jarak jauh diterima untuk '{device_id}': {data}")
            elif len(parts) >= 2:
                # Update properti tunggal (contoh path: "lampu_ruang_tamu/state", data: "on")
                prop_name = parts[1]
                self.db.update_device_state(device_id, {prop_name: data}, sync_to_cloud=False)
                print(f"[FirebaseCloud] 📥 Kontrol jarak jauh diterima: '{device_id}.{prop_name}' = {data}")
        except Exception as e:
            pass

    def stop(self):
        self.is_running = False
