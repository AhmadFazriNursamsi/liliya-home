import collections
import math
import os
import struct
import time
import wave
import io
import numpy as np
import pyaudio

TARGET_RATE = 16000

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

        self.native_rate = 16000
        self.stream = None
        self.p = None
        
        try:
            self.p = pyaudio.PyAudio()
            self._open_input_stream()
        except Exception as e:
            print(f"[AudioEngine Warning] Inisialisasi PyAudio: {e}")

        self.native_chunk_size = int(1280 * (self.native_rate / self.target_rate))
        self.rolling_buffer = collections.deque(maxlen=20)

    def _open_input_stream(self):
        if not self.p:
            return

        # Cek apakah device index tersedia
        dev_indices_to_try = [self.input_device] if self.input_device is not None else [None]
        
        try:
            count = self.p.get_device_count()
            for i in range(count):
                info = self.p.get_device_info_by_index(i)
                if info.get('maxInputChannels', 0) > 0 and i not in dev_indices_to_try:
                    dev_indices_to_try.append(i)
        except Exception:
            pass

        for dev_idx in dev_indices_to_try:
            for rate in [16000, 48000, 44100, 32000]:
                try:
                    frames_buf = int(1280 * (rate / self.target_rate))
                    stream = self.p.open(
                        format=pyaudio.paInt16,
                        channels=self.channels,
                        rate=rate,
                        input=True,
                        input_device_index=dev_idx,
                        frames_per_buffer=frames_buf
                    )
                    self.stream = stream
                    self.native_rate = rate
                    print(f"[AudioEngine] 🎙️ Mikrofon aktif pada device={dev_idx} ({rate} Hz)")
                    return
                except Exception:
                    continue

        print("[AudioEngine] ℹ️ Mode Web Mikrofon Aktif (Bicara melalui browser HP / Laptop)")

    def _resample_to_16k(self, audio_array: np.ndarray) -> np.ndarray:
        if self.native_rate == 16000 or len(audio_array) == 0:
            return audio_array
            
        if self.native_rate == 48000:
            usable_len = len(audio_array) - (len(audio_array) % 3)
            if usable_len == 0:
                return audio_array
            return np.mean(audio_array[:usable_len].reshape(-1, 3), axis=1).astype(np.int16)
        elif self.native_rate == 32000:
            usable_len = len(audio_array) - (len(audio_array) % 2)
            return np.mean(audio_array[:usable_len].reshape(-1, 2), axis=1).astype(np.int16)
        else:
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
        if not self.stream:
            time.sleep(0.08)
            return b'\x00' * 2560, np.zeros(1280, dtype=np.int16), 0.0

        try:
            raw_native = self.stream.read(self.native_chunk_size, exception_on_overflow=False)
            native_array = np.frombuffer(raw_native, dtype=np.int16)
            audio_16k = self._resample_to_16k(native_array)
            raw_16k = audio_16k.tobytes()
            rms = self.calculate_rms(audio_16k)
            self.rolling_buffer.append(raw_16k)
            return raw_16k, audio_16k, rms
        except Exception:
            time.sleep(0.02)
            return b'\x00' * 2560, np.zeros(1280, dtype=np.int16), 0.0

    def record_voice_command(self, initial_wait_timeout=3.5) -> bytes:
        if not self.stream:
            return b""

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
            wf.setsampwidth(2)
            wf.setframerate(self.target_rate)
            wf.writeframes(b''.join(frames))

        return wav_io.getvalue()

    def play_beep(self, frequency=880, duration=0.08, volume=0.25):
        pass

    def close(self):
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        if self.p:
            try:
                self.p.terminate()
            except Exception:
                pass
