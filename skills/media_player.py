import glob
import os
import shutil
import signal
import subprocess
import threading
import time

RADIO_STATIONS = {
    "prambors": "https://stream.radiojar.com/7csmg90fuqruv",
    "gen fm": "https://stream.radiojar.com/078r4uvfuqruv",
    "jak fm": "https://stream.radiojar.com/84u8v8vfuqruv",
    "hard rock": "https://stream.radiojar.com/v2m6b4vfuqruv",
    "elshinta": "https://stream.elshinta.com/live"
}

class MediaPlayerSkill:
    def __init__(self, config):
        self.config = config.get("skills", {})
        self.current_process = None
        self.current_title = ""
        self.is_paused = False
        self.lock = threading.Lock()
        self.cache_dir = "/tmp/stb_assistant_music"
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_player_cmd(self, file_or_url: str) -> list[str]:
        if shutil.which("mpv"):
            return [
                "mpv",
                "--no-video",
                "--really-quiet",
                "--ao=alsa",
                "--audio-device=alsa/plughw:1,0",
                "--volume=20",
                file_or_url
            ]
        elif shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", "20", file_or_url]
        return ["mpv", "--no-video", file_or_url]

    def pause_music(self):
        """Menjeda pemutaran musik seketika (0 ms) saat pengguna memanggil 'Alexa'."""
        with self.lock:
            if self.current_process and self.current_process.poll() is None and not self.is_paused:
                try:
                    os.kill(self.current_process.pid, signal.SIGSTOP)
                    self.is_paused = True
                    print(f"[MediaPlayer] ⏸️ Musik dijeda sementara pada posisi saat ini: {self.current_title}")
                except Exception as e:
                    print(f"[MediaPlayer Warning] Gagal pause: {e}")

    def resume_music(self):
        """Melanjutkan pemutaran musik dari detik terakhir yang dijeda setelah asisten selesai menjawab."""
        with self.lock:
            if self.current_process and self.current_process.poll() is None and self.is_paused:
                try:
                    os.kill(self.current_process.pid, signal.SIGCONT)
                    self.is_paused = False
                    print(f"[MediaPlayer] ▶️ Melanjutkan musik dari posisi terakhir: {self.current_title}")
                except Exception as e:
                    print(f"[MediaPlayer Warning] Gagal resume: {e}")

    def stop_music(self) -> str:
        """Menghentikan pemutaran musik secara permanen."""
        with self.lock:
            self.is_paused = False
            if self.current_process:
                try:
                    self.current_process.kill()
                except Exception:
                    pass
                self.current_process = None
                
            try:
                subprocess.run(["pkill", "-9", "mpv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "afplay"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "ffplay"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
                
            title = self.current_title
            self.current_title = ""
            return f"Musik '{title}' telah dihentikan." if title else "Musik telah dihentikan."

    def is_playing(self) -> bool:
        with self.lock:
            return self.current_process is not None and self.current_process.poll() is None

    def play_radio(self, station_name: str) -> str:
        """Memutar siaran radio online Indonesia."""
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

        def _play():
            with self.lock:
                self.current_title = f"Radio {station_name}"
                self.is_paused = False
                cmd = self._get_player_cmd(stream_url)
                self.current_process = subprocess.Popen(cmd)

        threading.Thread(target=_play, daemon=True).start()
        return f"Memutar radio {station_name} sekarang."

    def play_youtube(self, query: str) -> str:
        """Mencari dan memutar audio lagu dari YouTube berdasarkan judul/penyanyi."""
        self.stop_music()
        search_query = query.strip()
        if not any(w in search_query.lower() for w in ["lagu", "song", "dan", "official", "audio"]):
            search_query += " audio"
            
        print(f"[MediaPlayer] 🔍 Mengunduh lagu: {search_query}")

        def _fetch_and_play():
            try:
                # 1. Bersihkan file lama di cache
                for f in glob.glob(f"{self.cache_dir}/*"):
                    try:
                        os.remove(f)
                    except Exception:
                        pass

                # 2. Dapatkan judul video
                title_cmd = [
                    "yt-dlp",
                    "--get-title",
                    "--no-playlist",
                    "--default-search", "ytsearch",
                    f"ytsearch1:{search_query}"
                ]
                res_title = subprocess.run(title_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=6)
                title = res_title.stdout.strip() if (res_title and res_title.stdout) else query

                # 3. Download audio single track (max 15MB)
                output_template = os.path.join(self.cache_dir, "song.%(ext)s")
                dl_cmd = [
                    "yt-dlp",
                    "--extractor-args", "youtube:player_client=mweb,android",
                    "-f", "ba/b",
                    "--max-filesize", "15M",
                    "--no-playlist",
                    "--default-search", "ytsearch",
                    "-o", output_template,
                    f"ytsearch1:{search_query}"
                ]
                
                subprocess.run(dl_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                
                # 4. Putar file hasil download
                downloaded_files = glob.glob(f"{self.cache_dir}/song.*")
                if downloaded_files and os.path.exists(downloaded_files[0]):
                    target_audio_file = downloaded_files[0]
                    with self.lock:
                        self.current_title = title
                        self.is_paused = False
                        cmd_play = self._get_player_cmd(target_audio_file)
                        self.current_process = subprocess.Popen(cmd_play)
                        print(f"[MediaPlayer] ▶️ Sedang Memutar: {title}")
                else:
                    print(f"[MediaPlayer Error] Audio gagal diunduh untuk '{search_query}'.")

            except Exception as e:
                print(f"[MediaPlayer Error] Exception: {e}")

        threading.Thread(target=_fetch_and_play, daemon=True).start()
        return f"Baik, memutar {query} untuk Anda."
