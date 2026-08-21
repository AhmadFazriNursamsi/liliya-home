import collections
import math
import os
import struct
import time
import wave
import io
import numpy as np
import pyaudio

TARGET_RATE = 16000  # Rate wajib untuk Vosk & Whisper

class AudioEngine:
    def __init__(self, config):
        self.config = config.get("audio", {})
        self.target_rate = TARGET_RATE
        self.channels = self.config.get("channels", 1)
        self.energy_threshold = self.config.get("energy_threshold", 25)
        self.silence_timeout = self.config.get("silence_timeout", 1.0)
        self.max_record_seconds = self.config.get("max_record_seconds", 6.0)
        self.input_device = self.config.get("input_device_index")
        self.output_device = self.config.get("output_device_index")

        self.p = pyaudio.PyAudio()
        self.native_rate = 16000
        self.stream = None
        self._open_input_stream()

        # Ukuran chunk native agar selalu ekuivalen tepat 1280 sampel di 16kHz (~80ms)
        self.native_chunk_size = int(1280 * (self.native_rate / self.target_rate))
        self.rolling_buffer = collections.deque(maxlen=20)

    def _open_input_stream(self):
        for rate in [16000, 48000, 44100, 32000, 22050, 8000]:
            try:
                frames_buf = int(1280 * (rate / self.target_rate))
                stream = self.p.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=rate,
                    input=True,
                    input_device_index=self.input_device,
                    frames_per_buffer=frames_buf
                )
                self.stream = stream
                self.native_rate = rate
                if rate != self.target_rate:
                    print(f"[AudioEngine] 🎙️ USB Mic native rate: {rate} Hz (Zero-Artifact Resampling ke 16000 Hz)")
                else:
                    print(f"[AudioEngine] 🎙️ USB Mic terbuka di {rate} Hz")
                return
            except Exception:
                continue
        print("[AudioEngine Error] ❌ Tidak ada sample rate yang cocok untuk USB Mic!")

    def _resample_to_16k(self, audio_array: np.ndarray) -> np.ndarray:
        """Konversi audio native ke 16kHz murni tanpa distorsi filter."""
        if self.native_rate == 16000 or len(audio_array) == 0:
            return audio_array
            
        if self.native_rate == 48000:
            # 48000 Hz -> 16000 Hz: Rata-rata 3 sampel berurutan (Sempurna & Nol Distorsi)
            usable_len = len(audio_array) - (len(audio_array) % 3)
            if usable_len == 0:
                return audio_array
            return np.mean(audio_array[:usable_len].reshape(-1, 3), axis=1).astype(np.int16)
            
        elif self.native_rate == 32000:
            # 32000 Hz -> 16000 Hz: Rata-rata 2 sampel
            usable_len = len(audio_array) - (len(audio_array) % 2)
            return np.mean(audio_array[:usable_len].reshape(-1, 2), axis=1).astype(np.int16)
            
        else:
            # Linear interpolation yang mulus untuk rate lain (misal 44100)
            orig_indices = np.linspace(0, len(audio_array) - 1, len(audio_array))
            target_len = int(len(audio_array) * (16000 / self.native_rate))
            new_indices = np.linspace(0, len(audio_array) - 1, target_len)
            return np.interp(new_indices, orig_indices, audio_array).astype(np.int16)

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
            raw_native = self.stream.read(self.native_chunk_size, exception_on_overflow=False)
            native_array = np.frombuffer(raw_native, dtype=np.int16)
            
            # Resample ke 16000 Hz murni
            audio_16k = self._resample_to_16k(native_array)
            raw_16k = audio_16k.tobytes()
            rms = self.calculate_rms(audio_16k)
            
            self.rolling_buffer.append(raw_16k)
            return raw_16k, audio_16k, rms
        except Exception:
            time.sleep(0.02)
            return b'\x00' * 2560, np.zeros(1280, dtype=np.int16), 0.0

    def record_voice_command(self, initial_wait_timeout=3.5) -> bytes:
        """Merekam perintah suara pengguna dalam format WAV 16kHz murni."""
        frames = list(self.rolling_buffer)
        silence_start = None
        start_time = time.time()
        has_voice = len(frames) > 0

        while True:
            raw_16k, audio_16k, rms = self.read_chunk()
            frames.append(raw_16k)

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

        if len(frames) < 8:
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
                if i < 100: env = i / 100.0
                elif i > num_samples - 100: env = (num_samples - i) / 100.0
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
