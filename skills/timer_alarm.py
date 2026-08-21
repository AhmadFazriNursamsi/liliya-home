import threading
import time

class TimerAlarmSkill:
    def __init__(self, tts_engine=None):
        self.tts = tts_engine
        self.active_timers = {}
        self.lock = threading.Lock()

    def set_timer(self, seconds: int, label: str = "Timer") -> str:
        """Menyetel timer hitung mundur dengan pengingat suara."""
        if seconds <= 0:
            return "Durasi timer harus lebih dari 0 detik."
            
        def _on_timer_expire():
            print(f"[Timer] ⏰ Waktu habis untuk: {label}")
            if self.tts:
                self.tts.speak(f"Perhatian, waktu untuk {label} sudah habis!")
            with self.lock:
                self.active_timers.pop(label, None)

        timer_thread = threading.Timer(seconds, _on_timer_expire)
        timer_thread.daemon = True
        
        with self.lock:
            # Batalkan jika sudah ada timer dengan label sama
            if label in self.active_timers:
                self.active_timers[label]["timer"].cancel()
                
            self.active_timers[label] = {
                "timer": timer_thread,
                "expires_at": time.time() + seconds,
                "seconds": seconds
            }
            timer_thread.start()

        menit = seconds // 60
        detik = seconds % 60
        durasi_teks = []
        if menit > 0:
            durasi_teks.append(f"{menit} menit")
        if detik > 0:
            durasi_teks.append(f"{detik} detik")
            
        return f"Baik, timer untuk {label} selama {' '.join(durasi_teks)} telah disetel."

    def cancel_timer(self, label: str = "Timer") -> str:
        """Membatalkan timer yang sedang aktif."""
        with self.lock:
            if label in self.active_timers:
                self.active_timers[label]["timer"].cancel()
                del self.active_timers[label]
                return f"Timer {label} telah dibatalkan."
            elif self.active_timers:
                # Batalkan semua jika hanya 1
                for k, v in list(self.active_timers.items()):
                    v["timer"].cancel()
                self.active_timers.clear()
                return "Semua timer yang aktif telah dibatalkan."
            return "Tidak ada timer yang sedang aktif."
