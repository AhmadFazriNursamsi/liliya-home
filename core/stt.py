import io
import re
import time
from faster_whisper import WhisperModel

INDONESIAN_SMART_HOME_PROMPT = (
    "Halo Budi, asisten pintar smart home rumah. "
    "Perintah suara bahasa Indonesia: buka pintu sekarang, tutup pintu, "
    "nyalakan lampu ruang tamu, matikan semua lampu, nyalakan lampu teras, "
    "berapa suhu kamar sekarang, cek status sensor pintu dan suhu ruangan, "
    "atur suhu AC ke 22 derajat, putar lagu, jam berapa sekarang."
)

# Kamus normalisasi fonetik pintar bahasa Indonesia
PHONETIC_CORRECTIONS = {
    r"\bbukap\b": "buka",
    r"\bsekehang\b": "sekarang",
    r"\bsekeang\b": "sekarang",
    r"\bsekaran\b": "sekarang",
    r"\bsetara\b": "sekarang",
    r"\bsetara ini\b": "sekarang",
    r"\bdefes\b": "device",
    r"\bdevais\b": "device",
    r"\bpintuk\b": "pintu",
    r"\bdi matikan\b": "matikan",
    r"\bdi nyalakan\b": "nyalakan",
    r"\bka mar\b": "kamar",
    r"\bruang tamu\b": "ruang tamu",
    r"\blampu tamu\b": "lampu ruang tamu",
}

class WhisperSTTEngine:
    def __init__(self, model_size="tiny", compute_type="int8"):
        print("[STTEngine] 🎙️ Memuat mesin Speech-to-Text Bahasa Indonesia (Beam Search 3)...")
        self.model = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        print("[STTEngine] ✅ Mesin STT Bahasa Indonesia (Beam Search 3 + Anti-Halusinasi) siap.")

    def transcribe(self, wav_bytes: bytes) -> str:
        if not wav_bytes or len(wav_bytes) < 3000:
            return ""
        try:
            # Gunakan Beam Search = 3 untuk akurasi tata bahasa dan kosa kata Indonesia maksimal
            segments, info = self.model.transcribe(
                io.BytesIO(wav_bytes),
                language="id",
                beam_size=3,
                best_of=3,
                temperature=0.0,
                vad_filter=True,
                initial_prompt=INDONESIAN_SMART_HOME_PROMPT
            )
            text = " ".join([s.text for s in segments]).strip()
            
            # 1. Bersihkan prefix kata pemicu ("budi" / "halo budi")
            cleaned_text = re.sub(r'^(halo budi|hey budi|hai budi|budi)[,\s]+', '', text, flags=re.IGNORECASE).strip()
            if not cleaned_text:
                cleaned_text = text
                
            # 2. Koreksi fonem otomatis
            for pattern, replacement in PHONETIC_CORRECTIONS.items():
                cleaned_text = re.sub(pattern, replacement, cleaned_text, flags=re.IGNORECASE)
                
            return cleaned_text.strip()
        except Exception as e:
            print(f"[STTEngine Error] {e}")
            return ""
