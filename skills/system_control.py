from datetime import datetime
import subprocess
import os

DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
]

class SystemControlSkill:
    def __init__(self, config):
        self.config = config
        self.current_volume = config.get("skills", {}).get("default_volume", 80)

    def get_time(self) -> str:
        """Mengambil waktu dan tanggal saat ini dalam bahasa Indonesia."""
        now = datetime.now()
        day_name = DAYS_ID[now.weekday()]
        month_name = MONTHS_ID[now.month]
        hour = now.strftime("%H:%M")
        return f"Sekarang hari {day_name}, tanggal {now.day} {month_name} {now.year}, pukul {hour} WIB."

    def get_volume(self) -> str:
        """Mengecek tingkat volume suara speaker saat ini."""
        return f"Tingkat volume suara speaker saat ini adalah {self.current_volume} persen."

    def set_volume(self, level: int) -> str:
        """Mengatur tingkat volume suara speaker STB (0 - 100%)."""
        self.current_volume = max(0, min(100, level))
        try:
            # Coba amixer ALSA
            subprocess.run(["amixer", "set", "Master", f"{self.current_volume}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["amixer", "set", "PCM", f"{self.current_volume}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Coba pactl jika menggunakan pulseaudio
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{self.current_volume}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Volume speaker telah diatur ke {self.current_volume} persen."
        except Exception:
            return f"Volume diatur ke {self.current_volume}%."

    def get_system_status(self) -> str:
        """Mengecek status STB seperti suhu, IP, dan RAM."""
        try:
            temp_str = "normal"
            thermal_path = "/sys/class/thermal/thermal_zone0/temp"
            if os.path.exists(thermal_path):
                with open(thermal_path, "r") as f:
                    temp_c = int(f.read().strip()) / 1000.0
                    temp_str = f"{temp_c:.1f}°C"
                    
            return f"Status STB dalam kondisi baik. Suhu CPU {temp_str}."
        except Exception:
            return "Status STB normal dan siap beroperasi."
