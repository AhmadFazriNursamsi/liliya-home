import asyncio
import glob
import os
import random
import shutil
import subprocess
import tempfile
import edge_tts
from core.audio_hw import auto_unmute_hardware_audio, find_active_playback_device
from core.remote_tts import BROKER, split_for_speech

class TTSEngine:
    def __init__(self, config):
        self.config = config.get("tts", {})
        self.voice = self.config.get("voice", "id-ID-ArdiNeural")
        self.rate = self.config.get("rate", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = os.path.join(self.base_dir, "sounds")
        self.filler_files = [f for f in glob.glob(os.path.join(self.sounds_dir, "*.mp3")) if not os.path.basename(f).lower().startswith("adzan")]
        # Filler dengan suara kustom (dibuat oleh pekerja GPU), dipakai selama pekerja terhubung
        self.custom_filler_dir = os.path.join(self.base_dir, "data", "voice", "fillers")

        # Pastikan DAC hardware selalu terbuka dan unmuted sejak inisialisasi
        auto_unmute_hardware_audio()

    def play_startup_beep(self):
        """Memutar nada startup langsung ke speaker STB."""
        auto_unmute_hardware_audio()
        target_dev = find_active_playback_device()
        if os.path.exists("/tmp/test_beep.wav") and shutil.which("aplay"):
            for dev in [target_dev, "plughw:1,0", "plughw:0,0", "default"]:
                try:
                    res = subprocess.run(["aplay", "-D", dev, "/tmp/test_beep.wav"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if res.returncode == 0:
                        break
                except Exception:
                    pass

    def _play_audio_file(self, file_path: str):
        """Putar file audio MP3/WAV langsung ke Colokan AV STB / Speaker Aktif."""
        auto_unmute_hardware_audio()
        target_dev = find_active_playback_device()

        # 1. Konversi ke WAV 48kHz dengan volume 50% dan putar via aplay
        if shutil.which("ffmpeg") and shutil.which("aplay"):
            try:
                wav_tmp = file_path + ".wav"
                subprocess.run(
                    ["ffmpeg", "-y", "-i", file_path, "-filter:a", "volume=0.50", "-ar", "48000", "-ac", "2", wav_tmp],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if os.path.exists(wav_tmp):
                    for dev in [target_dev, "plughw:1,0", "plughw:0,0", "default"]:
                        res = subprocess.run(
                            ["aplay", "-D", dev, wav_tmp],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        if res.returncode == 0:
                            break
                    try:
                        os.remove(wav_tmp)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        # 2. Fallback mpv dengan alsa target device volume 50%
        if shutil.which("mpv"):
            try:
                res = subprocess.run(
                    ["mpv", "--no-video", "--really-quiet", "--ao=alsa", f"--audio-device=alsa/{target_dev}", "--volume=50", file_path],
                    capture_output=True
                )
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. Fallback ffplay
        if shutil.which("ffplay"):
            try:
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", "50", file_path],
                    capture_output=True
                )
            except Exception:
                pass

    def play_filler(self):
        """Memutar feedback jeda instan ('Sebentar ya...', 'Tunggu sebentar...')."""
        if not self.filler_files:
            self.filler_files = [f for f in glob.glob(os.path.join(self.sounds_dir, "*.mp3")) if not os.path.basename(f).lower().startswith("adzan")]
        files = self.filler_files
        if BROKER.online():
            files = glob.glob(os.path.join(self.custom_filler_dir, "*.mp3")) or files
        if files:
            chosen = random.choice(files)
            print(f"[TTS] ⏳ Respon Jeda Instan: {os.path.basename(chosen)}")
            try:
                self._play_audio_file(chosen)
            except Exception as e:
                print(f"[TTS Warning] Gagal memutar filler: {e}")

    async def _synthesize_to_file(self, text: str, output_path: str):
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch
        )
        await communicate.save(output_path)

    def _speak_remote(self, text: str) -> bool:
        """Suara kustom dari pekerja GPU, per kalimat: kalimat berikutnya dibuat selagi kalimat ini diputar.

        Return False bila belum ada satu kalimat pun yang terputar (pemanggil memakai Edge TTS untuk semuanya).
        Bila gagal di tengah jalan, sisa kalimat dibacakan Edge TTS dan return True.
        """
        chunks = split_for_speech(text)
        if not chunks:
            return True
        jobs = [BROKER.submit(c) for c in chunks]
        for i, job in enumerate(jobs):
            # waktu tunggu: kalimat pertama ~2 dtk di GPU + jaringan; sisanya dibuat selagi yang sebelumnya diputar
            timeout = 9.0 + 0.04 * len(job.text)
            if not job.done.wait(timeout) or not job.audio:
                if not job.done.is_set():
                    BROKER.stats["timeouts"] += 1
                reason = job.error or "terlambat"
                for rest in jobs[i:]:
                    BROKER.cancel(rest)
                BROKER.penalize(60)
                print(f"[TTS] ⚠️ Suara kustom gagal ({reason}); pakai Edge TTS.")
                if i == 0:
                    return False
                self._speak_edge(" ".join(j.text for j in jobs[i:]))
                return True
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
                tmp_file.write(job.audio)
                tmp_path = tmp_file.name
            try:
                self._play_audio_file(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        return True

    def speak(self, text: str):
        """Sintesis teks menjadi suara dan memutarnya ke speaker AV STB."""
        if not text or not text.strip():
            return
        print(f"[TTS] 🗣️ Membacakan: \"{text}\"")
        if BROKER.online():
            try:
                if self._speak_remote(text):
                    return
            except Exception as e:
                print(f"[TTS Warning] Suara kustom error: {e}")
        self._speak_edge(text)

    def _speak_edge(self, text: str):
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
                tmp_path = tmp_file.name
            asyncio.run(self._synthesize_to_file(text, tmp_path))
            self._play_audio_file(tmp_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            print(f"[TTS Error] Gagal memutar suara: {e}")
