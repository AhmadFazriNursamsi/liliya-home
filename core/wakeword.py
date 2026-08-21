import json
import os
import numpy as np
import vosk

class LiveSTTStreamer:
    def __init__(self, model):
        self.model = model
        self.rec = vosk.KaldiRecognizer(self.model, 16000)
        self.last_partial = ""
        self.full_transcript = []

    def process_chunk(self, raw_data: bytes) -> str:
        if self.rec.AcceptWaveform(raw_data):
            res = json.loads(self.rec.Result())
            text = res.get("text", "").strip()
            if text:
                self.full_transcript.append(text)
                self.last_partial = ""
                return " ".join(self.full_transcript)
        else:
            partial = json.loads(self.rec.PartialResult()).get("partial", "").strip()
            if partial and partial != self.last_partial:
                self.last_partial = partial
                return " ".join(self.full_transcript + [partial]).strip()
        return ""

    def get_final_text(self) -> str:
        res = json.loads(self.rec.FinalResult())
        final_t = res.get("text", "").strip()
        if final_t:
            self.full_transcript.append(final_t)
        return " ".join(self.full_transcript).strip()

class WakeWordDetector:
    def __init__(self, config):
        self.config = config.get("wakeword", {})
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.vosk_dir = os.path.join(self.base_dir, "models", "vosk-model-small-en-us-0.15")
        
        self.rec = None
        self.model = None
        self._init_detector()

    def _init_detector(self):
        if os.path.exists(self.vosk_dir):
            try:
                vosk.SetLogLevel(-1)
                self.model = vosk.Model(self.vosk_dir)
                grammar = '["budi", "halo budi", "hey budi", "hai budi", "alexa", "[unk]"]'
                self.rec = vosk.KaldiRecognizer(self.model, 16000, grammar)
                print("[WakeWordDetector] 🎯 Vosk KWS Siap & Aktif.")
                return
            except Exception as e:
                print(f"[WakeWordDetector Warning] Gagal inisialisasi Vosk: {e}")

    def create_stt_streamer(self) -> LiveSTTStreamer:
        if self.model:
            return LiveSTTStreamer(self.model)
        return None

    def process(self, audio_array: np.ndarray, is_media_playing: bool = False) -> bool:
        """
        Mendeteksi kata pemicu ("Budi" / "Halo Budi").
        Jika musik/berita sedang diputar (is_media_playing=True), gunakan filter ketat
        agar obrolan pembaca berita di speaker tidak memicu asisten secara tidak sengaja.
        """
        if not self.rec:
            return False
            
        try:
            raw_bytes = audio_array.tobytes()
            if self.rec.AcceptWaveform(raw_bytes):
                res = json.loads(self.rec.Result())
                text = res.get("text", "").strip().lower()
                
                if not text:
                    return False
                
                # Jika berita/musik sedang berputar: Wajib panggilan bersih dan jelas
                if is_media_playing:
                    # Tolak jika kalimat panjang berisi banyak unknown dari berita (misal: "[unk] [unk] budi [unk] [unk]")
                    unk_count = text.count("[unk]")
                    words = [w for w in text.split() if w != "[unk]"]
                    
                    if unk_count > 2 and len(words) <= 1:
                        # Ini suara latar belakang dari berita/TV, abaikan
                        return False
                        
                    if text in ["budi", "halo budi", "hey budi", "hai budi", "alexa"] or ("budi" in words and len(words) <= 3):
                        print(f"\n[WakeWordDetector] 🎯 KATA PEMICU RESMI TERDETEKSI (Saat Media Aktif): '{text}'!")
                        self.reset()
                        return True
                else:
                    words = text.split()
                    if any(w in words for w in ["budi", "alexa"]) or "halo budi" in text or "hey budi" in text or "hai budi" in text:
                        print(f"\n[WakeWordDetector] 🎯 KATA PEMICU RESMI TERDETEKSI: '{text}'!")
                        self.reset()
                        return True
        except Exception:
            pass
            
        return False

    def reset(self):
        if self.model:
            grammar = '["budi", "halo budi", "hey budi", "hai budi", "alexa", "[unk]"]'
            self.rec = vosk.KaldiRecognizer(self.model, 16000, grammar)
