import asyncio
import glob
import io
import os
import random
import shutil
import subprocess
import tempfile
import threading
import edge_tts

class TTSEngine:
    def __init__(self, config):
        self.config = config.get("tts", {})
        self.voice = self.config.get("voice", "id-ID-GadisNeural")
        self.rate = self.config.get("rate", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = os.path.join(self.base_dir, "sounds")
        self.filler_files = glob.glob(os.path.join(self.sounds_dir, "*.mp3"))

    def _play_audio_file(self, file_path: str):
        if shutil.which("afplay"):
            subprocess.run(["afplay", file_path], check=True)
        elif shutil.which("mpv"):
            subprocess.run(["mpv", "--no-video", "--really-quiet", "--volume=100", file_path], check=True)
        elif shutil.which("ffplay"):
            subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", file_path], check=True)
        elif shutil.which("aplay"):
            subprocess.run(["aplay", file_path], check=True)

    def play_filler(self):
        """Memutar feedback sapaan/pengisi jeda instan ('Sebentar ya...', 'Tunggu sebentar...') dengan latensi 0 detik."""
        if not self.filler_files:
            self.filler_files = glob.glob(os.path.join(self.sounds_dir, "*.mp3"))
            
        if self.filler_files:
            chosen = random.choice(self.filler_files)
            print(f"[TTS] ⏳ Respon Jeda Instan: {os.path.basename(chosen)}")
            try:
                self._play_audio_file(chosen)
            except Exception as e:
                print(f"[TTS Warning] Gagal memutar filler sound: {e}")

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
                
            # Jalankan coroutine edge-tts
            asyncio.run(self._synthesize_to_file(text, tmp_path))
            
            # Putar audio
            self._play_audio_file(tmp_path)
                
            # Hapus file temporary
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            print(f"[TTS Error] Gagal memutar suara: {e}")
