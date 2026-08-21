import collections
import math
import os
import struct
import time
import wave
import io
import numpy as np
import pyaudio

class AudioEngine:
    def __init__(self, config):
        self.config = config.get("audio", {})
        self.sample_rate = self.config.get("sample_rate", 16000)
        self.channels = self.config.get("channels", 1)
        self.chunk_size = self.config.get("chunk_size", 1280)
        self.energy_threshold = self.config.get("energy_threshold", 40)
        self.silence_timeout = self.config.get("silence_timeout", 1.0)
        self.max_record_seconds = self.config.get("max_record_seconds", 6.0)
        
        self.input_device = self.config.get("input_device_index")
        self.output_device = self.config.get("output_device_index")
        
        # Buffer bergulir untuk menyimpan audio 1.5 detik terakhir secara kontinu
        self.rolling_buffer = collections.deque(maxlen=18)
        
        self.p = pyaudio.PyAudio()
        self.stream = None
        self._open_input_stream()
        
    def _open_input_stream(self):
        try:
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.input_device,
                frames_per_buffer=self.chunk_size
            )
        except Exception as e:
            print(f"[AudioEngine Error] Gagal membuka stream mikrofon: {e}")

    def flush(self):
        """Membersihkan buffer antrean mikrofon tanpa memblokir."""
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
            audio_array = np.frombuffer(raw_data, dtype=np.int16)
            rms = self.calculate_rms(audio_array)
            self.rolling_buffer.append(raw_data)
            return raw_data, audio_array, rms
        except Exception:
            time.sleep(0.02)
            return b'\x00' * (self.chunk_size * 2), np.zeros(self.chunk_size, dtype=np.int16), 0.0

    def record_voice_command(self, initial_wait_timeout=3.5) -> bytes:
        """
        Merekam kelanjutan suara pengguna setelah kata pemicu terdeteksi.
        Menggabungkan audio buffer 1.5 detik sebelumnya sehingga kalimat yang diucapkan
        langsung setelah kata 'Budi' tidak akan hilang/terpotong.
        """
        # Masukkan seluruh frame sebelum wake word selesai terdeteksi
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

        # Kemas ke format WAV
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))
            
        return wav_io.getvalue()

    def play_beep(self, frequency=880, duration=0.08, volume=0.25):
        """Memutar nada beep lembut non-blocking / super cepat."""
        try:
            sample_rate = 22050
            num_samples = int(duration * sample_rate)
            raw_audio = bytearray()
            
            for i in range(num_samples):
                env = 1.0
                if i < 100:
                    env = i / 100.0
                elif i > num_samples - 100:
                    env = (num_samples - i) / 100.0
                    
                sample = volume * env * math.sin(2 * math.pi * frequency * (i / sample_rate))
                raw_audio += struct.pack('<h', int(sample * 32767.0))
                
            out_stream = self.p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                output=True,
                output_device_index=self.output_device
            )
            out_stream.write(bytes(raw_audio))
            out_stream.stop_stream()
            out_stream.close()
        except Exception:
            pass

    def close(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.p.terminate()
