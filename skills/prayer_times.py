import datetime
import json
import os
import requests
import threading
import time

PRAYER_NAMES_ID = {
    "Fajr": "Subuh",
    "Sunrise": "Terbit",
    "Dhuhr": "Dzuhur",
    "Asr": "Ashar",
    "Maghrib": "Maghrib",
    "Isha": "Isya"
}

PRAYER_ORDER = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

class PrayerTimesSkill:
    def __init__(self, config, tts_engine=None, media_player=None, event_bridge=None):
        self.config = config.get("skills", {})
        self.default_city = self.config.get("default_city", "Jakarta")
        self.tts = tts_engine
        self.media_player = media_player
        self.event_bridge = event_bridge
        
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = os.path.join(self.base_dir, "sounds")
        self.adzan_file = os.path.join(self.sounds_dir, "adzan.mp3")
        self.adzan_subuh_file = os.path.join(self.sounds_dir, "adzan_subuh.mp3")
        
        # Cache for prayer timings
        self.cached_timings = {}
        self.cached_date = None
        self.last_triggered_key = None
        self.is_playing_adhan = False
        
        # Background scheduler thread
        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        print(f"[PrayerTimes] 🕌 Fitur Jadwal Sholat & Adzan Otomatis aktif untuk: {self.default_city}")

    def set_event_bridge(self, event_bridge):
        self.event_bridge = event_bridge

    def _fetch_prayer_times(self, city: str = "") -> dict:
        """Mengambil jadwal sholat hari ini dari API Aladhan (Kemenag/SIHAT Method 11)."""
        target_city = city if city and city.strip() else self.default_city
        today_str = datetime.date.today().isoformat()
        
        # Gunakan cache jika sudah ada untuk hari ini & kota yang sama
        if self.cached_date == today_str and self.cached_timings.get("city") == target_city:
            return self.cached_timings.get("timings", {})
            
        try:
            url = f"https://api.aladhan.com/v1/timingsByCity?city={target_city}&country=Indonesia&method=11"
            res = requests.get(url, timeout=6)
            if res.status_code == 200:
                data = res.json()
                timings = data.get("data", {}).get("timings", {})
                
                # Format ke HH:MM bersih
                clean_timings = {}
                for k, v in timings.items():
                    # Ambil hanya HH:MM (buang (WIB) jika ada)
                    clean_timings[k] = v.split()[0][:5]
                    
                self.cached_timings = {
                    "city": target_city,
                    "timings": clean_timings
                }
                self.cached_date = today_str
                return clean_timings
        except Exception as e:
            print(f"[PrayerTimes Warning] Gagal mengambil API jadwal sholat: {e}")
            
        # Fallback cache lama jika tersedia
        return self.cached_timings.get("timings", {})

    def get_prayer_times(self, city: str = "") -> str:
        """Mengambil jadwal lengkap 5 waktu sholat hari ini beserta waktu sholat terdekat."""
        target_city = city if city and city.strip() else self.default_city
        timings = self._fetch_prayer_times(target_city)
        if not timings:
            return f"Maaf, tidak dapat mengambil jadwal sholat untuk wilayah {target_city} saat ini."
            
        subuh = timings.get("Fajr", "--:--")
        terbit = timings.get("Sunrise", "--:--")
        dzuhur = timings.get("Dhuhr", "--:--")
        ashar = timings.get("Asr", "--:--")
        maghrib = timings.get("Maghrib", "--:--")
        isya = timings.get("Isha", "--:--")
        
        next_prayer_info = self._calculate_next_prayer(timings)
        
        response = (
            f"Jadwal sholat wilayah {target_city} hari ini: "
            f"Subuh {subuh}, Terbit {terbit}, Dzuhur {dzuhur}, Ashar {ashar}, "
            f"Maghrib {maghrib}, dan Isya {isya} WIB. {next_prayer_info}"
        )
        return response

    def get_next_prayer(self, city: str = "") -> str:
        """Mengambil informasi waktu sholat berikutnya yang akan tiba."""
        target_city = city if city and city.strip() else self.default_city
        timings = self._fetch_prayer_times(target_city)
        if not timings:
            return f"Maaf, tidak dapat mengecek waktu sholat berikutnya untuk wilayah {target_city}."
            
        next_info = self._calculate_next_prayer(timings)
        return f"Untuk wilayah {target_city}: {next_info}"

    def _calculate_next_prayer(self, timings: dict) -> str:
        """Menghitung waktu sholat berikutnya dan selisih waktu."""
        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        for p_key in PRAYER_ORDER:
            t_str = timings.get(p_key)
            if t_str and ":" in t_str:
                h, m = map(int, t_str.split(":"))
                p_minutes = h * 60 + m
                if p_minutes > current_minutes:
                    diff = p_minutes - current_minutes
                    hours_left = diff // 60
                    mins_left = diff % 60
                    
                    time_desc = []
                    if hours_left > 0:
                        time_desc.append(f"{hours_left} jam")
                    if mins_left > 0:
                        time_desc.append(f"{mins_left} menit")
                    time_str = " ".join(time_desc) if time_desc else "kurang dari 1 menit"
                    
                    p_name_id = PRAYER_NAMES_ID.get(p_key, p_key)
                    return f"Waktu sholat berikutnya adalah {p_name_id} pada pukul {t_str} WIB (sekitar {time_str} lagi)."
                    
        # Jika semua waktu sholat hari ini sudah lewat, maka berikutnya adalah Subuh besok
        subuh_str = timings.get("Fajr", "04:30")
        return f"Semua waktu sholat hari ini telah terlewati. Waktu sholat berikutnya adalah Subuh besok pukul {subuh_str} WIB."

    def play_adhan(self, prayer_name: str = "") -> str:
        """
        HANYA panggil fungsi ini jika pengguna secara EKSPLISIT meminta MEMUTAR kumandang Adzan (contoh: 'putar adzan', 'kumandangkan adzan Subuh', 'bunyikan adzan').
        DILARANG KERAS memanggil fungsi ini saat pengguna meminta mematikan lampu, mengontrol smart home, atau mematikan musik.
        """
        p_name_lower = prayer_name.lower() if prayer_name else ""
        is_subuh = "subuh" in p_name_lower or "fajr" in p_name_lower
        audio_file = self.adzan_subuh_file if (is_subuh and os.path.exists(self.adzan_subuh_file)) else self.adzan_file
        p_display = PRAYER_NAMES_ID.get(prayer_name, prayer_name.capitalize() if prayer_name else "Makkah")
        
        if self.media_player:
            self.media_player.play_file(audio_file, title=f"🕌 Kumandang Adzan {p_display}")
            return f"Baik, memutar kumandang adzan {p_display} sekarang."
        return "Pemutar audio tidak tersedia."

    def _trigger_adhan_audio(self, prayer_name: str = ""):
        """Eksekusi pemutaran audio adzan saat jadwal tiba via MediaPlayerSkill."""
        p_name_lower = prayer_name.lower() if prayer_name else ""
        is_subuh = "subuh" in p_name_lower or "fajr" in p_name_lower
        audio_file = self.adzan_subuh_file if (is_subuh and os.path.exists(self.adzan_subuh_file)) else self.adzan_file
        p_display = PRAYER_NAMES_ID.get(prayer_name, prayer_name.capitalize() if prayer_name else "Fardhu")
        
        if self.media_player:
            self.media_player.play_file(audio_file, title=f"🕌 Kumandang Adzan {p_display}")

    def _scheduler_loop(self):
        """Loop latar belakang untuk mengecek waktu adzan secara akurat setiap 20 detik."""
        while self.is_running:
            try:
                now = datetime.datetime.now()
                now_str = now.strftime("%H:%M")
                today_str = now.date().isoformat()
                
                timings = self._fetch_prayer_times(self.default_city)
                if timings:
                    for p_key in PRAYER_ORDER:
                        p_time = timings.get(p_key)
                        if p_time == now_str:
                            trigger_key = f"{today_str}_{p_key}_{now_str}"
                            if self.last_triggered_key != trigger_key:
                                self.last_triggered_key = trigger_key
                                p_name = PRAYER_NAMES_ID.get(p_key, p_key)
                                
                                print(f"\n[PrayerTimes] 🔔 WAKTU SHOLAT TIBA: {p_name} ({now_str} WIB) di {self.default_city}!")
                                
                                # Jeda musik jika ada
                                if self.media_player and self.media_player.is_playing():
                                    self.media_player.pause_music()
                                    
                                reminder_text = f"Waktu sholat {p_name} untuk wilayah {self.default_city} dan sekitarnya telah tiba. Mari kita menunaikan ibadah sholat."
                                
                                if self.event_bridge:
                                    self.event_bridge.broadcast_state("speaking")
                                    self.event_bridge.broadcast_assistant_reply(f"🕌 {reminder_text}")
                                    
                                # 1. Bacakan pengingat adzan melalui suara asisten BUDI
                                if self.tts:
                                    self.tts.speak(reminder_text)
                                    time.sleep(0.5)
                                    
                                # 2. Langsung kumandangkan Adzan
                                self._trigger_adhan_audio(p_name)
                                
            except Exception as e:
                print(f"[PrayerTimes Scheduler Error] {e}")
                
            time.sleep(20)
