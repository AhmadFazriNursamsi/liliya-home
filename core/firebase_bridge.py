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
        self._recent_local_pushes = {}
        self._send_lock = threading.Lock()  # PATCH berurutan: perintah cepat beruntun tidak boleh tiba terbalik
        
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

        self._recent_local_pushes[device_id] = time.time()

        def _send():
            try:
                url = self._get_url(device_id)
                with self._send_lock:
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
            
            # Abaikan pantulan / echo dari perintah lokal yang baru saja dikirim (< 5 detik)
            last_push = self._recent_local_pushes.get(device_id, 0)
            if (time.time() - last_push) < 5.0:
                return

            local_dev = self.db.get_device_by_id(device_id)
            if not local_dev:
                return

            if len(parts) == 1:
                # Update objek dictionary device utuh (contoh: data={"state": "on", ...})
                if isinstance(data, dict):
                    # Hanya data yang jelas lebih baru dari status lokal yang boleh menggerakkan perangkat.
                    # Data tanpa last_updated (pantulan lama / snapshot usang) dulu tetap diterapkan dan
                    # bisa menyalakan/mematikan lampu fisik dengan status kedaluwarsa.
                    cloud_ts = data.get("last_updated") or 0
                    if not isinstance(cloud_ts, (int, float)) or cloud_ts <= local_dev.get("last_updated", 0):
                        return
                    updates = {k: v for k, v in data.items() if k != "last_updated" and local_dev.get(k) != v}
                    if not updates:
                        return
                    print(f"[FirebaseCloud] 📥 Kontrol jarak jauh diterima untuk '{device_id}': {updates}")
                    self.db.update_device_state(device_id, updates, sync_to_cloud=False, source="Firebase")
            elif len(parts) >= 2:
                # Update properti tunggal (contoh path: "lampu_ruang_tamu/state", data: "on")
                prop_name = parts[1]
                if prop_name == "last_updated":
                    return
                # Jika properti sudah sama persis dengan yang ada di lokal, jangan eksekusi ulang
                if local_dev.get(prop_name) == data:
                    return
                self.db.update_device_state(device_id, {prop_name: data}, sync_to_cloud=False, source="Firebase")
                print(f"[FirebaseCloud] 📥 Kontrol jarak jauh diterima: '{device_id}.{prop_name}' = {data}")
        except Exception as e:
            print(f"[FirebaseCloud Error] Event cloud gagal diproses: {e}")

    def stop(self):
        self.is_running = False
