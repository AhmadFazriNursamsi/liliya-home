import asyncio
import datetime
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

WEATHER_CODES = {
    0: "Cerah tanpa awan",
    1: "Sebagian besar cerah",
    2: "Sebagian berawan",
    3: "Mendung dan berawan",
    45: "Berkabut",
    48: "Kabut tebal",
    51: "Gerimis ringan",
    53: "Gerimis sedang",
    55: "Gerimis lebat",
    61: "Hujan ringan",
    63: "Hujan sedang",
    65: "Hujan lebat",
    80: "Hujan rintik-rintik",
    81: "Hujan deras",
    95: "Badai petir"
}

BMKG_FORECAST_URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={adm4}"
BMKG_NOWCAST_URL = "https://www.bmkg.go.id/alerts/nowcast/id"
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
# Kode cuaca BMKG yang berarti hujan (60-an hujan, 80-an hujan lokal/lebat, 95-97 petir)
RAIN_CODES = {60, 61, 63, 65, 80, 81, 82, 95, 97}


def _point_in_polygon(lat: float, lon: float, polygon_text: str) -> bool:
    """Polygon CAP: 'lat,lon lat,lon ...'. Ray casting sederhana."""
    pts = []
    for pair in polygon_text.split():
        try:
            a, b = pair.split(",")
            pts.append((float(a), float(b)))
        except ValueError:
            continue
    inside = False
    for i in range(len(pts)):
        (y1, x1), (y2, x2) = pts[i - 1], pts[i]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


class WeatherSkill:
    """Cuaca rumah dari BMKG (prakiraan per kelurahan + peringatan dini), dengan pengumuman
    hujan lewat speaker seperti pengingat adzan dan notifikasi ke HP."""

    def __init__(self, config, tts_engine=None, media_player=None, event_bridge=None, web_server=None):
        self.default_city = config.get("skills", {}).get("default_city", "Jakarta")
        cfg = config.get("weather", {}) or {}
        self.adm4 = str(cfg.get("adm4", "")).strip()
        self.lat = cfg.get("lat")
        self.lon = cfg.get("lon")
        self.alerts_enabled = bool(cfg.get("rain_alerts", True))
        self.lookahead_hours = float(cfg.get("lookahead_hours", 3))
        self.check_interval = int(cfg.get("check_interval", 900))
        self.announce_start = str(cfg.get("announce_start", "05:00"))
        self.announce_end = str(cfg.get("announce_end", "21:00"))
        self.tts = tts_engine
        self.media_player = media_player
        self.event_bridge = event_bridge
        self.web_server = web_server

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.state_path = os.path.join(self.base_dir, "data", "weather_state.json")
        self._forecast = None
        self._forecast_at = 0
        self._lock = threading.Lock()

        if self.adm4:
            print(f"[Weather] 🌦️ Cuaca BMKG aktif untuk kode wilayah {self.adm4}"
                  f"{' + peringatan hujan' if self.alerts_enabled else ''}")
            if self.alerts_enabled:
                threading.Thread(target=self._alert_loop, daemon=True).start()

    def set_event_bridge(self, event_bridge):
        self.event_bridge = event_bridge

    # ---------- Data BMKG ----------
    def _bmkg_forecast(self) -> dict:
        """Prakiraan 3 harian BMKG (per 3 jam). Disimpan 30 menit; BMKG membatasi 60 permintaan/menit."""
        with self._lock:
            if self._forecast and time.time() - self._forecast_at < 1800:
                return self._forecast
            res = requests.get(BMKG_FORECAST_URL.format(adm4=self.adm4), timeout=10)
            res.raise_for_status()
            data = (res.json().get("data") or [{}])[0]
            slots = []
            for day in data.get("cuaca") or []:
                for s in day:
                    try:
                        s["_dt"] = datetime.datetime.strptime(s["local_datetime"], "%Y-%m-%d %H:%M:%S")
                    except (KeyError, ValueError):
                        continue
                    slots.append(s)
            slots.sort(key=lambda s: s["_dt"])
            self._forecast = {"lokasi": data.get("lokasi") or {}, "slots": slots}
            self._forecast_at = time.time()
            return self._forecast

    @staticmethod
    def _is_rain(slot: dict) -> bool:
        return slot.get("weather") in RAIN_CODES or float(slot.get("tp") or 0) >= 1.0

    @staticmethod
    def _rain_desc(slot: dict) -> str:
        # Curah hujan tercatat tapi kode cuacanya bukan hujan (mis. "Berawan" + tp 1.2 mm)
        if slot.get("weather") in RAIN_CODES:
            return (slot.get("weather_desc") or "hujan").lower()
        return "hujan ringan"

    def _place_name(self, lokasi: dict) -> str:
        desa = lokasi.get("desa", "")
        kec = lokasi.get("kecamatan", "")
        return f"{desa}, {kec}" if desa and kec else (desa or kec or "rumah")

    # ---------- Tool untuk Gemini ----------
    def get_weather(self, city: str = "") -> str:
        """Cuaca saat ini dan prakiraan hujan beberapa jam ke depan. Kosongkan city untuk cuaca di rumah
        (data BMKG per kelurahan); isi city hanya bila pengguna menyebut kota lain."""
        if self.adm4 and not (city and city.strip()):
            try:
                return self._home_weather_text()
            except Exception as e:
                print(f"[Weather] Gagal mengambil BMKG: {e}")
        return self._city_weather_text(city)

    def _home_weather_text(self) -> str:
        fc = self._bmkg_forecast()
        now = datetime.datetime.now()
        slots = fc["slots"]
        if not slots:
            raise RuntimeError("prakiraan BMKG kosong")
        current = min(slots, key=lambda s: abs((s["_dt"] - now).total_seconds()))
        place = self._place_name(fc["lokasi"])
        text = (f"Menurut BMKG, cuaca di {place} sekitar pukul {current['_dt']:%H.%M} {current.get('weather_desc', '').lower()}, "
                f"suhu {current.get('t')} derajat Celsius, kelembapan {current.get('hu')}%.")
        upcoming = [s for s in slots if now < s["_dt"] <= now + datetime.timedelta(hours=12)]
        rain = [s for s in upcoming if self._is_rain(s)]
        if rain:
            parts = [f"{self._rain_desc(s)} sekitar pukul {s['_dt']:%H.%M}" for s in rain[:3]]
            text += " Dalam 12 jam ke depan diperkirakan " + ", lalu ".join(parts) + "."
        elif upcoming:
            text += " Dalam 12 jam ke depan tidak ada prakiraan hujan."
        return text

    def _city_weather_text(self, city: str) -> str:
        """Kota lain (atau BMKG tidak tersedia): Open-Meteo."""
        target_city = city if city and city.strip() else self.default_city
        try:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={target_city}&count=1&language=id&format=json"
            geo_resp = requests.get(geo_url, timeout=5).json()
            if not geo_resp.get("results"):
                return f"Maaf, informasi cuaca untuk kota {target_city} tidak ditemukan."
            loc = geo_resp["results"][0]
            weather_url = (f"https://api.open-meteo.com/v1/forecast?latitude={loc['latitude']}&longitude={loc['longitude']}"
                           "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&timezone=auto")
            current = requests.get(weather_url, timeout=5).json().get("current", {})
            condition = WEATHER_CODES.get(current.get("weather_code", 0), "Cerah berawan")
            return (f"Cuaca di {loc.get('name', target_city)} saat ini {condition}, suhu sekitar "
                    f"{current.get('temperature_2m', '--')} derajat Celsius dengan kelembapan {current.get('relative_humidity_2m', '--')}%.")
        except Exception:
            return f"Maaf, terjadi kendala saat mengecek cuaca {target_city}."

    # ---------- Peringatan hujan otomatis ----------
    def _load_state(self) -> dict:
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_path)

    def _alert_loop(self):
        time.sleep(30)  # biarkan layanan lain siap dulu
        while True:
            state = self._load_state()
            announced = state.setdefault("announced", {})
            try:
                self._check_forecast(announced)
            except Exception as e:
                print(f"[Weather] Cek prakiraan BMKG gagal: {e}")
            try:
                self._check_nowcast(announced)
            except Exception as e:
                print(f"[Weather] Cek peringatan dini BMKG gagal: {e}")
            # Buang catatan lebih dari 2 hari
            cutoff = time.time() - 2 * 86400
            state["announced"] = {k: v for k, v in announced.items() if v > cutoff}
            self._save_state(state)
            time.sleep(self.check_interval)

    def _check_forecast(self, announced: dict):
        fc = self._bmkg_forecast()
        now = datetime.datetime.now()
        window_end = now + datetime.timedelta(hours=self.lookahead_hours)
        speak_now = self._in_announce_hours()
        for slot in fc["slots"]:
            if not (now < slot["_dt"] <= window_end) or not self._is_rain(slot):
                continue
            stamp = f"{slot['_dt']:%Y%m%d%H}"
            prev_stamp = f"{slot['_dt'] - datetime.timedelta(hours=3):%Y%m%d%H}"
            # Push ke HP sekali per hujan; suara di speaker juga sekali, tapi ditunda bila sedang jam tenang
            if f"fc-{prev_stamp}" in announced:
                announced[f"fc-{stamp}"] = announced.get(f"fc-{stamp}", time.time())
                announced[f"fcs-{stamp}"] = announced.get(f"fcs-{stamp}", time.time())
                continue  # masih bagian dari hujan yang sudah diumumkan
            need_push = f"fc-{stamp}" not in announced
            need_speak = speak_now and f"fcs-{stamp}" not in announced
            if not (need_push or need_speak):
                continue
            announced[f"fc-{stamp}"] = announced.get(f"fc-{stamp}", time.time())
            if speak_now:
                announced[f"fcs-{stamp}"] = time.time()
            desc = self._rain_desc(slot)
            place = self._place_name(fc["lokasi"])
            self._announce(
                f"🌧️ Prakiraan {desc}",
                f"BMKG memperkirakan {desc} di {place} sekitar pukul {slot['_dt']:%H.%M}.",
                f"Perhatian. BMKG memperkirakan {desc} di sekitar rumah, kira-kira pukul {slot['_dt']:%H.%M}. "
                f"Jangan lupa angkat jemuran dan tutup jendela.",
                push=need_push, speak=need_speak)
            return  # cukup satu pengumuman per pengecekan

    def _check_nowcast(self, announced: dict):
        if self.lat is None or self.lon is None:
            return
        fc = self._forecast or {}
        lokasi = fc.get("lokasi") or {}
        provinsi = (lokasi.get("provinsi") or "").lower()
        kecamatan = (lokasi.get("kecamatan") or "").upper()
        res = requests.get(BMKG_NOWCAST_URL, timeout=15)
        res.raise_for_status()
        channel = ET.fromstring(res.content).find("channel")
        for item in channel.findall("item") if channel is not None else []:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            if provinsi and provinsi not in title.lower():
                continue
            key = "nc-" + link.rsplit("/", 1)[-1]
            if key in announced or not link.startswith("https://www.bmkg.go.id/"):
                continue
            cap = ET.fromstring(requests.get(link, timeout=15).content)
            info = cap.find("cap:info", CAP_NS)
            if info is None:
                continue
            expires = info.findtext("cap:expires", "", CAP_NS)
            try:
                exp_dt = datetime.datetime.fromisoformat(expires)
                if exp_dt.timestamp() < time.time():
                    announced[key] = time.time()
                    continue
            except ValueError:
                exp_dt = None
            desc = info.findtext("cap:description", "", CAP_NS)
            hit = bool(kecamatan) and re.search(r"\b" + re.escape(kecamatan) + r"\b", desc.upper()) is not None
            if not hit:
                hit = any(_point_in_polygon(float(self.lat), float(self.lon), p.text or "")
                          for p in info.findall("cap:area/cap:polygon", CAP_NS))
            announced[key] = time.time()
            if not hit:
                continue
            event = info.findtext("cap:event", "Cuaca ekstrem", CAP_NS)
            until = f" hingga pukul {exp_dt:%H.%M}" if exp_dt else ""
            self._announce(
                f"⚠️ Peringatan dini BMKG: {event}",
                f"{event} berpotensi terjadi di wilayah rumah{until}. Tetap waspada.",
                f"Peringatan dini dari BMKG. {event} berpotensi terjadi di wilayah rumah{until}. "
                f"Mohon tetap waspada dan kurangi aktivitas di luar rumah.",
                warning=True)

    def _in_announce_hours(self) -> bool:
        now = datetime.datetime.now().strftime("%H:%M")
        return self.announce_start <= now < self.announce_end

    def _announce(self, title: str, body: str, speech: str, warning: bool = False, push: bool = True, speak: bool = None):
        """Seperti pengingat adzan: tampil di aplikasi, dibacakan di speaker (jam tertentu), dan push ke HP."""
        print(f"[Weather] 🔔 {title}: {body}")
        if self.event_bridge:
            self.event_bridge.broadcast_assistant_reply(f"{'⚠️' if warning else '🌧️'} {body}")
        if push:
            self._push(title, body)
        if speak is None:
            speak = self._in_announce_hours()
        if not (self.tts and speak):
            return
        paused = False
        if self.media_player and self.media_player.is_playing():
            self.media_player.pause_music()
            paused = True
        if self.event_bridge:
            self.event_bridge.broadcast_state("speaking")
        self.tts.speak(speech)
        if self.event_bridge:
            self.event_bridge.broadcast_state("idle")
        if paused and self.media_player.is_paused:
            self.media_player.resume_music()

    def _push(self, title: str, body: str):
        ws = self.web_server
        push = getattr(ws, "push", None)
        if not push or not getattr(ws, "loop", None):
            return
        if not push.settings().get("weather", True):
            return
        fut = asyncio.run_coroutine_threadsafe(
            push.send(title, body, data={"type": "weather", "cam": "cuaca", "open": "home"}), ws.loop)
        try:
            fut.result(timeout=30)
        except Exception as e:
            print(f"[Weather] Notifikasi gagal: {e}")
