import glob
import os
import shutil
import signal
import subprocess
import threading
import time
from core.audio_hw import find_active_playback_device, auto_unmute_hardware_audio

RADIO_STATIONS = {
    "prambors": "https://stream.radiojar.com/7csmg90fuqruv",
    "gen fm": "https://stream.radiojar.com/078r4uvfuqruv",
    "jak fm": "https://stream.radiojar.com/84u8v8vfuqruv",
    "hard rock": "https://stream.radiojar.com/v2m6b4vfuqruv",
    "elshinta": "https://stream.elshinta.com/live"
}

class MediaPlayerSkill:
    def __init__(self, config, event_bridge=None):
        self.config = config.get("skills", {})
        self.event_bridge = event_bridge
        self.current_process = None
        self.current_title = ""
        self.is_paused = False
        self.lock = threading.Lock()
        self.cache_dir = "/tmp/stb_assistant_music"
        os.makedirs(self.cache_dir, exist_ok=True)

    def set_event_bridge(self, event_bridge):
        self.event_bridge = event_bridge

    def _wait_for_tts_finish(self):
        """Menunggu sampai suara balasan TTS (aplay) selesai berbicara di speaker."""
        for _ in range(40):
            res = subprocess.run(["pgrep", "aplay"], capture_output=True, text=True)
            if not res.stdout.strip():
                break
            time.sleep(0.15)
        time.sleep(0.3)

    def _get_player_cmd(self, file_or_url: str) -> list[str]:
        target_dev = find_active_playback_device()
        if shutil.which("mpv"):
            return [
                "mpv",
                "--no-video",
                "--really-quiet",
                "--ao=alsa",
                f"--audio-device=alsa/{target_dev}",
                "--volume=50",
                file_or_url
            ]
        elif shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", "50", file_or_url]
        return ["mpv", "--no-video", file_or_url]

    def pause_music(self):
        with self.lock:
            if self.current_process and self.current_process.poll() is None and not self.is_paused:
                try:
                    os.kill(self.current_process.pid, signal.SIGSTOP)
                    self.is_paused = True
                    print(f"[MediaPlayer] ⏸️ Musik dijeda: {self.current_title}")
                except Exception as e:
                    print(f"[MediaPlayer Warning] Gagal pause: {e}")

    def resume_music(self):
        with self.lock:
            if self.current_process and self.current_process.poll() is None and self.is_paused:
                try:
                    os.kill(self.current_process.pid, signal.SIGCONT)
                    self.is_paused = False
                    print(f"[MediaPlayer] ▶️ Melanjutkan musik: {self.current_title}")
                except Exception as e:
                    print(f"[MediaPlayer Warning] Gagal resume: {e}")

    def stop_music(self) -> str:
        with self.lock:
            self.is_paused = False
            if self.current_process:
                try:
                    self.current_process.terminate()
                    self.current_process.wait(timeout=1.0)
                except Exception:
                    try:
                        self.current_process.kill()
                    except Exception:
                        pass
                self.current_process = None
                
            try:
                subprocess.run(["pkill", "-9", "mpv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "ffplay"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
                
            time.sleep(0.3)
            title = self.current_title
            self.current_title = ""
            if self.event_bridge:
                self.event_bridge.broadcast_now_playing("")
            return f"Musik '{title}' telah dihentikan." if title else "Musik telah dihentikan."

    def is_playing(self) -> bool:
        with self.lock:
            return self.current_process is not None and self.current_process.poll() is None

    def play_radio(self, station_name: str) -> str:
        self.stop_music()
        station_key = station_name.lower().strip()
        stream_url = None
        
        for name, url in RADIO_STATIONS.items():
            if name in station_key or station_key in name:
                stream_url = url
                station_name = name.title()
                break
                
        if not stream_url:
            station_name = "Prambors FM"
            stream_url = RADIO_STATIONS["prambors"]

        title_display = f"Radio {station_name}"
        self.current_title = title_display
        if self.event_bridge:
            self.event_bridge.broadcast_now_playing(f"⏳ Memutar: {title_display}")

        def _play():
            self._wait_for_tts_finish()
            auto_unmute_hardware_audio()
            with self.lock:
                self.is_paused = False
                cmd = self._get_player_cmd(stream_url)
                self.current_process = subprocess.Popen(cmd)
                print(f"[MediaPlayer] 📻 Sedang Memutar Radio di Speaker STB: {station_name}")
                if self.event_bridge:
                    self.event_bridge.broadcast_now_playing(f"📻 {title_display}")

        threading.Thread(target=_play, daemon=True).start()
        return f"Memutar radio {station_name} sekarang."

    def play_youtube(self, query: str) -> str:
        self.stop_music()
        search_query = query.strip()
        if not any(w in search_query.lower() for w in ["lagu", "song", "dan", "official", "audio"]):
            search_query += " audio"
            
        title_display = query.title()
        self.current_title = title_display
        if self.event_bridge:
            self.event_bridge.broadcast_now_playing(f"⏳ Memuat: {title_display}...")
            
        print(f"[MediaPlayer] 🔍 Mencari lagu YouTube untuk Speaker STB: '{search_query}'")

        def _fetch_and_play():
            try:
                yt_bin = shutil.which("yt-dlp") or "/usr/local/bin/yt-dlp" or "yt-dlp"
                
                # 1. Ekstraksi URL streaming langsung bersih (seperti di test_music.py)
                stream_cmd = [
                    yt_bin,
                    "-f", "ba/b",
                    "-g",
                    "--default-search", "ytsearch",
                    f"ytsearch1:{search_query}"
                ]
                res_url = subprocess.run(stream_cmd, capture_output=True, text=True, timeout=20)
                stream_url = res_url.stdout.strip().splitlines()[0] if (res_url and res_url.stdout) else ""

                # 2. Tunggu sampai suara respon BUDI (TTS aplay) selesai membaca di speaker STB
                self._wait_for_tts_finish()
                auto_unmute_hardware_audio()

                if stream_url:
                    with self.lock:
                        self.is_paused = False
                        cmd_play = self._get_player_cmd(stream_url)
                        self.current_process = subprocess.Popen(cmd_play)
                        print(f"[MediaPlayer] ▶️ Memutar Lagu di Speaker AV STB: {title_display}")
                        if self.event_bridge:
                            self.event_bridge.broadcast_now_playing(f"🎵 {title_display}")
                        return

                # 3. Fallback: Download cepat ke /tmp dan putar file lokal
                print(f"[MediaPlayer] ⬇️ Mengunduh audio fallback untuk '{title_display}'...")
                dl_file = os.path.join(self.cache_dir, "current_song.opus")
                if os.path.exists(dl_file):
                    try: os.remove(dl_file)
                    except: pass

                dl_cmd = [
                    yt_bin,
                    "-f", "ba/b",
                    "-o", dl_file,
                    f"ytsearch1:{search_query}"
                ]
                subprocess.run(dl_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)

                if os.path.exists(dl_file):
                    with self.lock:
                        self.is_paused = False
                        cmd_play = self._get_player_cmd(dl_file)
                        self.current_process = subprocess.Popen(cmd_play)
                        print(f"[MediaPlayer] ▶️ Memutar Audio Lokal di Speaker STB: {title_display}")
                        if self.event_bridge:
                            self.event_bridge.broadcast_now_playing(f"🎵 {title_display}")
                        return

            except Exception as e:
                print(f"[MediaPlayer Error] {e}")

        threading.Thread(target=_fetch_and_play, daemon=True).start()
        return f"Baik, memutar {query} untuk Anda."
