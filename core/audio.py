import collections
import math
import os
import struct
import time
import wave
import io
import numpy as np
import pyaudio
from scipy import signal as scipy_signal

TARGET_RATE = 16000  # Rate yang dibutuhkan Whisper & Vosk

class AudioEngine:
    def __init__(self, config):
        self.config = config.get("audio", {})
        self.target_rate = TARGET_RATE
        self.channels = self.config.get("channels", 1)
        self.energy_threshold = self.config.get("energy_threshold", 40)
        self.silence_timeout = self.config.get("silence_timeout", 1.0)
        self.max_record_seconds = self.config.get("max_record_seconds", 6.0)
        self.input_device = self.config.get("input_device_index")
        self.output_device = self.config.get("output_device_index")

        self.p = pyaudio.PyAudio()
        self.native_rate = None
        self.stream = None
        self._open_input_stream()

        # Ukuran chunk disesuaikan dengan native rate agar ekuivalen ~80ms audio
        self.chunk_size = int(self.native_rate * 0.08) if self.native_rate else 1280
        self.rolling_buffer = collections.deque(maxlen=20)

    def _open_input_stream(self):
        # Coba berbagai sample rate yang umum didukung USB mic
        for rate in [16000, 44100, 48000, 32000, 22050, 8000]:
            try:
                stream = self.p.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=rate,
                    input=True,
                    input_device_index=self.input_device,
                    frames_per_buffer=int(rate * 0.08)
                )
                self.stream = stream
                self.native_rate = rate
                if rate != self.target_rate:
                    print(f"[AudioEngine] 🎙️ USB Mic native rate: {rate} Hz (akan di-resample ke {self.target_rate} Hz)")
                else:
                    print(f"[AudioEngine] 🎙️ USB Mic terbuka di {rate} Hz")
                return
            except Exception:
                continue
        print("[AudioEngine Error] ❌ Tidak ada sample rate yang cocok untuk USB Mic!")

    def _resample(self, raw_bytes: bytes) -> bytes:
        """Resample audio dari native_rate ke 16000 Hz jika diperlukan."""
        if self.native_rate == self.target_rate:
            return raw_bytes
        audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
        resampled = scipy_signal.resample_poly(
            audio,
            self.target_rate,
            self.native_rate
        )
        return resampled.astype(np.int16).tobytes()

    def flush(self):
        if self.stream:
            try:
                available = self.stream.get_read_available()
                if available > 0:
                    self.stream.read(available, exception_on_overflow=False)
            except Exception:
                pass

    def calculate_rms(self, audio_data: np.ndarray) -> float:
        if len(audio_data) == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))

    def read_chunk(self) -> tuple[bytes, np.ndarray, float]:
        try:
            raw_data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            resampled = self._resample(raw_data)
            audio_array = np.frombuffer(resampled, dtype=np.int16)
            rms = self.calculate_rms(audio_array)
            self.rolling_buffer.append(resampled)
            return resampled, audio_array, rms
        except Exception:
            time.sleep(0.02)
            chunk = int(self.target_rate * 0.08)
            return b'\x00' * (chunk * 2), np.zeros(chunk, dtype=np.int16), 0.0

    def record_voice_command(self, initial_wait_timeout=3.5) -> bytes:
        """
        Merekam kelanjutan suara pengguna setelah kata pemicu terdeteksi.
        Audio sudah di-resample ke 16000 Hz siap langsung diproses Whisper.
        """
        frames = list(self.rolling_buffer)
        silence_start = None
        start_time = time.time()
        has_voice = len(frames) > 0

        while True:
            raw_data, audio_array, rms = self.read_chunk()
            frames.append(raw_data)

            if rms >= self.energy_threshold:
                has_voice = True
                silence_start = None
            else:
                if not has_voice:
                    if time.time() - start_time >= initial_wait_timeout:
                        break
                else:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= self.silence_timeout:
                        break

            if time.time() - start_time >= self.max_record_seconds:
                break

        if len(frames) < 10:
            return b""

        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.target_rate)
            wf.writeframes(b''.join(frames))

        return wav_io.getvalue()

    def play_beep(self, frequency=880, duration=0.08, volume=0.25):
        try:
            rate = 22050
            num_samples = int(duration * rate)
            raw_audio = bytearray()
            for i in range(num_samples):
                env = 1.0
                if i < 100:
                    env = i / 100.0
                elif i > num_samples - 100:
                    env = (num_samples - i) / 100.0
                sample = volume * env * math.sin(2 * math.pi * frequency * (i / rate))
                raw_audio += struct.pack('<h', int(sample * 32767.0))
            try:
                out = self.p.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                  output=True, output_device_index=self.output_device)
                out.write(bytes(raw_audio))
                out.stop_stream()
                out.close()
            except Exception:
                pass
        except Exception:
            pass

    def close(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.p.terminate()
