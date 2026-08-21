import asyncio
import glob
import io
import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
import threading
import edge_tts
import pyaudio

class TTSEngine:
    def __init__(self, config):
        self.config = config.get("tts", {})
        self.voice = self.config.get("voice", "id-ID-ArdiNeural")
        self.rate = self.config.get("rate", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = os.path.join(self.base_dir, "sounds")
        self.filler_files = glob.glob(os.path.join(self.sounds_dir, "*.mp3"))
        self._p = pyaudio.PyAudio()

    def play_startup_beep(self):
        """Memutar 2 nada beep pendek saat startup agar pengguna tahu speaker aktif."""
        for freq, dur in [(523, 0.08), (659, 0.12)]:
            self._play_beep_tone(freq, dur)
        print("[TTS] 🔔 Startup beep OK — speaker aktif!")

    def _play_beep_tone(self, frequency=880, duration=0.1, volume=0.3):
        try:
            rate = 44100
            num_samples = int(duration * rate)
            raw_audio = bytearray()
            for i in range(num_samples):
                env = 1.0
                if i < 200: env = i / 200.0
                elif i > num_samples - 200: env = (num_samples - i) / 200.0
                sample = volume * env * math.sin(2 * math.pi * frequency * (i / rate))
                raw_audio += struct.pack('<h', int(sample * 32767.0))
            try:
                stream = self._p.open(format=pyaudio.paInt16, channels=1, rate=rate, output=True)
                stream.write(bytes(raw_audio))
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        except Exception:
            pass

    def _play_audio_file(self, file_path: str):
        """Putar file audio MP3 menggunakan ffplay atau mpv."""
        # Coba ffplay terlebih dulu (lebih kompatibel dengan ALSA)
        if shutil.which("ffplay"):
            result = subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-af", "aresample=44100", file_path],
                capture_output=True
            )
            if result.returncode == 0:
                return
        # Fallback ke mpv tanpa ao constraint
        if shutil.which("mpv"):
            result = subprocess.run(
                ["mpv", "--no-video", "--really-quiet",
                 "--ao=alsa", "--audio-device=hw=0,0",
                 "--volume=100", file_path],
                capture_output=True
            )
            if result.returncode == 0:
                return
        # Fallback terakhir: aplay (untuk WAV)
        if shutil.which("aplay") and file_path.endswith(".wav"):
            subprocess.run(["aplay", "-D", "plughw:0,0", file_path], capture_output=True)

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
        """Sintesis teks menjadi suara dan memutarnya ke speaker."""
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
            # Fallback: beep jika TTS gagal
            self._play_beep_tone(440, 0.2)
