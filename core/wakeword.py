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
                grammar = '["budi", "buddy", "body", "boody", "halo", "hello", "hey", "hai", "alexa", "[unk]"]'
                self.rec = vosk.KaldiRecognizer(self.model, 16000, grammar)
                print("[WakeWordDetector] 🎯 Vosk KWS Siap & Aktif (Grammar: Budi / Halo Budi).")
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
        Mengecek baik Final Result maupun Partial Result secara instan.
        """
        if not self.rec:
            return False
            
        try:
            raw_bytes = audio_array.tobytes()
            
            if self.rec.AcceptWaveform(raw_bytes):
                res = json.loads(self.rec.Result())
                text = res.get("text", "").strip().lower()
            else:
                partial_res = json.loads(self.rec.PartialResult())
                text = partial_res.get("partial", "").strip().lower()
                
            if not text:
                return False
            
            words = text.split()
            valid_triggers = ["budi", "buddy", "body", "boody", "alexa"]
            
            # Jika berita/musik sedang berputar: Wajib panggilan bersih
            if is_media_playing:
                unk_count = text.count("[unk]")
                clean_words = [w for w in words if w != "[unk]"]
                
                if unk_count > 2 and len(clean_words) <= 1:
                    return False
                    
                if any(t in clean_words for t in valid_triggers) or "halo budi" in text or "hey budi" in text:
                    print(f"\n[WakeWordDetector] 🎯 KATA PEMICU TERDETEKSI (Saat Media Aktif): '{text}'!")
                    self.reset()
                    return True
            else:
                if any(t in words for t in valid_triggers) or "halo budi" in text or "hey budi" in text or "hai budi" in text or ("halo" in words and len(words) <= 2):
                    print(f"\n[WakeWordDetector] 🎯 KATA PEMICU RESMI TERDETEKSI: '{text}'!")
                    self.reset()
                    return True
                    
        except Exception:
            pass
            
        return False

    def reset(self):
        if self.model:
            grammar = '["budi", "buddy", "body", "boody", "halo", "hello", "hey", "hai", "alexa", "[unk]"]'
            self.rec = vosk.KaldiRecognizer(self.model, 16000, grammar)
