"""Suara Budi lewat ElevenLabs (API text-to-speech).

- Jawaban dipecah per kalimat dan dibuat paralel: kalimat pertama cepat terdengar, sisanya siap saat giliran.
- Kalimat pendek disimpan di cache disk (data/voice/elevenlabs/cache) sehingga kalimat yang berulang,
  seperti "Budi siap digunakan." atau konfirmasi lampu, tidak memakan kuota karakter lagi.
- Gagal (kuota habis, kunci ditolak, internet putus): TTSEngine kembali ke Edge TTS, lalu ElevenLabs
  diistirahatkan sebentar supaya Budi tidak menunggu berulang kali.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format={fmt}"
CACHE_MAX_CHARS = 160            # hanya kalimat pendek yang di-cache (jawaban panjang jarang berulang)
CACHE_MAX_FILES = 3000

_ANGKA = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def terbilang(n: int) -> str:
    if n < 12:
        return _ANGKA[n]
    if n < 20:
        return terbilang(n - 10) + " belas"
    if n < 100:
        return terbilang(n // 10) + " puluh" + (" " + terbilang(n % 10) if n % 10 else "")
    if n < 200:
        return "seratus" + (" " + terbilang(n - 100) if n > 100 else "")
    if n < 1000:
        return terbilang(n // 100) + " ratus" + (" " + terbilang(n % 100) if n % 100 else "")
    if n < 2000:
        return "seribu" + (" " + terbilang(n - 1000) if n > 1000 else "")
    if n < 1000000:
        return terbilang(n // 1000) + " ribu" + (" " + terbilang(n % 1000) if n % 1000 else "")
    if n < 1000000000:
        return terbilang(n // 1000000) + " juta" + (" " + terbilang(n % 1000000) if n % 1000000 else "")
    return " ".join(_ANGKA[int(d)] for d in str(n))


def spoken_text(t: str) -> str:
    """Teks jawaban -> teks lisan Indonesia. Model multibahasa sering membaca angka dalam bahasa Inggris,
    jadi jam, desimal, ribuan, persen, derajat, dan angka diubah menjadi kata lebih dulu."""
    t = re.sub(r"[*_`#>|]+", " ", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\b(\d{1,2})[.:](\d{2})\b(?!\.\d)", lambda m: f"{m[1]} lewat {m[2]}" if m[2] != "00" else m[1], t)
    t = re.sub(r"(\d+),(\d+)", lambda m: f"{m[1]} koma {' '.join(m[2])}", t)
    t = re.sub(r"(?<=\d)\.(?=\d{3}\b)", "", t)
    t = t.replace("°C", " derajat Celsius").replace("°", " derajat").replace("%", " persen")
    t = re.sub(r"\bRp\.?\s*(\d+)", r"\1 rupiah", t)
    t = re.sub(r"(?<=\d)\s*[–-]\s*(?=\d)", " sampai ", t)
    t = re.sub(r"\d+", lambda m: terbilang(int(m[0])), t)
    return re.sub(r"\s+", " ", t).strip()


class ElevenLabsError(Exception):
    def __init__(self, msg, fatal_for_s=60):
        super().__init__(msg)
        self.fatal_for_s = fatal_for_s


class ElevenLabsTTS:
    def __init__(self, config: dict, base_dir: str):
        cfg = (config.get("tts", {}) or {}).get("elevenlabs", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.api_key = str(cfg.get("api_key", "") or "")
        self.voice_id = str(cfg.get("voice_id", "") or "")
        self.voice_name = str(cfg.get("voice_name", "") or self.voice_id)
        self.model_id = str(cfg.get("model_id", "eleven_flash_v2_5"))
        self.language = str(cfg.get("language_code", "id") or "")
        self.fmt = str(cfg.get("output_format", "mp3_44100_128"))
        self.settings = {
            "stability": float(cfg.get("stability", 0.5)),
            "similarity_boost": float(cfg.get("similarity_boost", 0.75)),
            "style": float(cfg.get("style", 0.0)),
            "use_speaker_boost": bool(cfg.get("speaker_boost", True)),
            "speed": float(cfg.get("speed", 1.0)),
        }
        self.timeout = float(cfg.get("timeout_sec", 12))
        self.cache_dir = os.path.join(base_dir, "data", "voice", "elevenlabs", "cache")
        self.filler_dir = os.path.join(base_dir, "data", "voice", "elevenlabs", "fillers")
        self._pause_until = 0.0
        self.stats = {"requests": 0, "cache_hits": 0, "chars": 0, "errors": 0, "last_error": None}
        if self.enabled and not (self.api_key and self.voice_id):
            print("[ElevenLabs Warning] api_key / voice_id kosong. ElevenLabs dimatikan.")
            self.enabled = False
        if self.enabled:
            os.makedirs(self.cache_dir, exist_ok=True)
            print(f"[ElevenLabs] 🎙️ Suara Budi: {self.voice_name} ({self.model_id})")

    def available(self) -> bool:
        return self.enabled and time.time() >= self._pause_until

    def pause(self, seconds: float):
        self._pause_until = max(self._pause_until, time.time() + seconds)

    def _cache_path(self, text: str) -> str:
        key = json.dumps([self.voice_id, self.model_id, self.language, self.settings, text], sort_keys=True)
        return os.path.join(self.cache_dir, hashlib.sha256(key.encode()).hexdigest()[:32] + ".mp3")

    def synthesize(self, text: str) -> bytes:
        """Teks (satu potongan) -> MP3. Melempar ElevenLabsError bila gagal."""
        text = spoken_text(text)
        if not text:
            raise ElevenLabsError("teks kosong", 0)
        cache = self._cache_path(text) if len(text) <= CACHE_MAX_CHARS else None
        if cache and os.path.exists(cache):
            self.stats["cache_hits"] += 1
            try:
                os.utime(cache)
            except OSError:
                pass
            with open(cache, "rb") as f:
                return f.read()
        body = {"text": text, "model_id": self.model_id, "voice_settings": self.settings}
        if self.language:
            body["language_code"] = self.language
        req = urllib.request.Request(
            API.format(voice=self.voice_id, fmt=self.fmt), data=json.dumps(body).encode(),
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        )
        self.stats["requests"] += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            self.stats["errors"] += 1
            try:
                detail = json.loads(e.read()).get("detail", {})
            except Exception:
                detail = {}
            code = (detail.get("code") or detail.get("status") or "") if isinstance(detail, dict) else ""
            msg = (detail.get("message") if isinstance(detail, dict) else str(detail)) or ""
            self.stats["last_error"] = f"HTTP {e.code} {code}".strip()
            # kuota habis / kunci salah / paket tidak cukup: istirahat lama; sisanya sebentar
            long_pause = e.code in (401, 402, 403) or "quota" in (code + msg).lower()
            raise ElevenLabsError(f"HTTP {e.code} {code} {msg[:120]}".strip(), 1800 if long_pause else 60)
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = type(e).__name__
            raise ElevenLabsError(f"{type(e).__name__}: {e}", 60)
        if len(audio) < 200:
            raise ElevenLabsError("audio kosong", 60)
        self.stats["chars"] += len(text)
        if cache:
            tmp = cache + ".tmp"
            with open(tmp, "wb") as f:
                f.write(audio)
            os.replace(tmp, cache)
            self._prune_cache()
        return audio

    def _prune_cache(self):
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir) if f.endswith(".mp3")]
            if len(files) <= CACHE_MAX_FILES:
                return
            files.sort(key=os.path.getmtime)
            for f in files[: len(files) - CACHE_MAX_FILES]:
                os.remove(f)
        except OSError:
            pass

    def prefetch(self, chunks):
        """Mulai membuat semua potongan secara paralel (maks 2 sekaligus). Return daftar slot hasil."""
        slots = [{"done": threading.Event(), "audio": None, "error": None, "cancel": False} for _ in chunks]
        order = iter(range(len(chunks)))         # dikerjakan urut: kalimat awal selalu lebih dulu
        lock = threading.Lock()

        def work():
            while True:
                with lock:
                    i = next(order, None)
                if i is None:
                    return
                try:
                    if not slots[i]["cancel"]:
                        slots[i]["audio"] = self.synthesize(chunks[i])
                except ElevenLabsError as e:
                    slots[i]["error"] = e
                except Exception as e:
                    slots[i]["error"] = ElevenLabsError(str(e), 60)
                finally:
                    slots[i]["done"].set()

        for _ in range(min(2, len(chunks))):
            threading.Thread(target=work, daemon=True).start()
        return slots
