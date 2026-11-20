import datetime
import threading
import time
from typing import List


class LampSchedule:
    """Jadwal harian lampu dari config.yaml (bagian lamp_schedule), mis. nyalakan teras pukul 17:40.

    Status lampu Bluetooth tidak dilaporkan balik, jadi status di database bisa saja sudah "on"
    padahal lampunya mati. Karena itu perintah tetap dikirim ke lampu walau database tidak berubah."""

    def __init__(self, config: dict, smart_home_db):
        self.db = smart_home_db
        self.entries: List[dict] = []
        for e in config.get("lamp_schedule") or []:
            t = str(e.get("time", "")).strip()
            devices = [str(d) for d in e.get("devices") or []]
            state = str(e.get("state", "on")).lower()
            try:
                datetime.datetime.strptime(t, "%H:%M")
            except ValueError:
                print(f"[LampSchedule Warning] Jam tidak valid di lamp_schedule: {t!r}")
                continue
            if devices and state in ("on", "off"):
                self.entries.append({"time": t, "devices": devices, "state": state})
        self._done = set()
        if self.entries:
            desc = "; ".join(f"{e['time']} {e['state']} {', '.join(e['devices'])}" for e in self.entries)
            print(f"[LampSchedule] ⏰ Jadwal lampu aktif: {desc}")
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                now = datetime.datetime.now()
                now_str = now.strftime("%H:%M")
                for i, e in enumerate(self.entries):
                    key = f"{now.date()}-{i}"
                    if e["time"] == now_str and key not in self._done:
                        self._done.add(key)
                        self._run(e)
                # Simpan catatan hari ini saja
                self._done = {k for k in self._done if k.startswith(str(now.date()))}
            except Exception as ex:
                print(f"[LampSchedule Error] {ex}")
            time.sleep(20)

    def _run(self, entry: dict):
        print(f"[LampSchedule] 🔔 {entry['time']}: {entry['state']} {', '.join(entry['devices'])}")
        tuya = getattr(self.db, "tuya_bridge", None)
        for device_id in entry["devices"]:
            device = self.db.get_device_by_id(device_id)
            if not device:
                print(f"[LampSchedule Warning] Perangkat '{device_id}' tidak ditemukan")
                continue
            self.db.update_device_state(device_id, {"state": entry["state"]}, source=f"jadwal {entry['time']}")
            # Paksa kirim ke lampu walau database sudah mencatat status yang sama (dedup di bridge mencegah dobel)
            if tuya and device.get("tuya_id"):
                tuya.push_device_update(device_id, {"state": entry["state"]}, tuya_id=device["tuya_id"])
