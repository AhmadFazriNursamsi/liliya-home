import asyncio
import glob
import os
import random
import shutil
import subprocess
import tempfile
import edge_tts

class TTSEngine:
    def __init__(self, config):
        self.config = config.get("tts", {})
        self.voice = self.config.get("voice", "id-ID-ArdiNeural")
        self.rate = self.config.get("rate", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = os.path.join(self.base_dir, "sounds")
        self.filler_files = glob.glob(os.path.join(self.sounds_dir, "*.mp3"))

    def play_startup_beep(self):
        """Memutar nada startup langsung ke colokan AV STB."""
        if os.path.exists("/tmp/test_beep.wav") and shutil.which("aplay"):
            try:
                subprocess.run(["aplay", "-D", "plughw:1,0", "/tmp/test_beep.wav"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def _play_audio_file(self, file_path: str):
        """Putar file audio MP3/WAV langsung ke Colokan AV STB (plughw:1,0)."""
        # 1. Konversi ke WAV 48kHz dan putar via aplay (Paling Andal & Terbukti Berbunyi di STB)
        if shutil.which("ffmpeg") and shutil.which("aplay"):
            try:
                wav_tmp = file_path + ".wav"
                subprocess.run(
                    ["ffmpeg", "-y", "-i", file_path, "-ar", "48000", "-ac", "2", wav_tmp],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if os.path.exists(wav_tmp):
                    subprocess.run(
                        ["aplay", "-D", "plughw:1,0", wav_tmp],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    os.remove(wav_tmp)
                    return
            except Exception:
                pass

        # 2. Fallback mpv dengan alsa plughw:1,0
        if shutil.which("mpv"):
            try:
                res = subprocess.run(
                    ["mpv", "--no-video", "--really-quiet", "--ao=alsa", "--audio-device=alsa/plughw:1,0", "--volume=100", file_path],
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
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", file_path],
                    capture_output=True
                )
            except Exception:
                pass

    def play_filler(self):
        """Memutar feedback jeda instan ('Sebentar ya...', 'Tunggu sebentar...')."""
        if not self.filler_files:
            self.filler_files = glob.glob(os.path.join(self.sounds_dir, "*.mp3"))
        if self.filler_files:
            chosen = random.choice(self.filler_files)
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

    def speak(self, text: str):
        """Sintesis teks menjadi suara dan memutarnya ke speaker AV STB."""
        if not text or not text.strip():
            return
        print(f"[TTS] 🗣️ Membacakan: \"{text}\"")
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
                tmp_path = tmp_file.name
            asyncio.run(self._synthesize_to_file(text, tmp_path))
            self._play_audio_file(tmp_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            print(f"[TTS Error] Gagal memutar suara: {e}")
