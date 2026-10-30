"""Suara kustom Budi lewat pekerja GPU jarak jauh (mis. notebook molab dengan VoxCPM2 + LoRA).

STB terlalu lemah untuk model suara besar, jadi sintesis dikerjakan di GPU. Pekerja tidak perlu membuka
port: ia yang menghubungi Liliya (long-poll) untuk mengambil teks, lalu mengirim balik audionya.

Alur:
  TTSEngine.speak() -> BROKER.submit(kalimat) -> pekerja GET /api/voice-worker/next
                    <- event job selesai      <- pekerja POST /api/voice-worker/result/{id} (audio/mpeg)

Kalau pekerja tidak terhubung atau terlambat, TTSEngine kembali ke Edge TTS sehingga Budi tidak pernah diam.
"""
import asyncio
import hmac
import os
import re
import threading
import time
import uuid
from collections import deque

from aiohttp import web

WORKER_PREFIX = "/api/voice-worker/"
ONLINE_GRACE_S = 10          # pekerja dianggap masih terhubung selama ini setelah long-poll terakhir selesai
MAX_POLL_S = 25              # di bawah batas timeout Cloudflare (100 dtk)
MAX_AUDIO_BYTES = 20 * 1024 * 1024
JOB_TTL_S = 120


def split_for_speech(text: str, first_max: int = 60, max_len: int = 220):
    """Pecah jawaban jadi potongan kalimat. Potongan pertama dibuat pendek supaya suara cepat mulai."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    chunks, cur = [], ""
    for s in sentences:
        limit = first_max if not chunks else max_len
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    # kalimat tunggal yang sangat panjang: potong di koma terdekat
    out = []
    for c in chunks:
        while len(c) > max_len + 60:
            cut = c.rfind(", ", 0, max_len)
            cut = cut + 1 if cut > 40 else max_len
            out.append(c[:cut].strip())
            c = c[cut:].strip()
        out.append(c)
    return out


class _Job:
    __slots__ = ("id", "text", "created", "taken_at", "done", "audio", "error", "cancelled")

    def __init__(self, text):
        self.id = uuid.uuid4().hex[:16]
        self.text = text
        self.created = time.time()
        self.taken_at = None
        self.done = threading.Event()
        self.audio = None
        self.error = None
        self.cancelled = False


class VoiceBroker:
    """Antrean thread-safe antara TTSEngine (thread biasa) dan handler aiohttp (event loop)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = deque()
        self._jobs = {}
        self._active_polls = 0
        self._last_poll_end = 0.0
        self._last_seen = 0.0
        self._penalty_until = 0.0
        self.enabled = False
        self.token = ""
        self.base_dir = ""
        self.worker = {}               # info yang dikirim pekerja (nama suara, GPU)
        self.stats = {"served": 0, "failed": 0, "timeouts": 0, "last_latency": None, "last_rtf": None}

    def configure(self, config: dict, base_dir: str):
        cfg = (config.get("tts", {}) or {}).get("remote", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.token = str(cfg.get("token", "") or "")
        self.base_dir = base_dir
        if self.enabled and len(self.token) < 24:
            print("[VoiceBroker Warning] tts.remote.token kosong/terlalu pendek. Suara kustom dimatikan.")
            self.enabled = False
        if self.enabled:
            print("[VoiceBroker] 🎙️ Suara kustom aktif: menunggu pekerja GPU terhubung.")

    # ---------- dipakai TTSEngine ----------
    def online(self) -> bool:
        if not self.enabled or time.time() < self._penalty_until:
            return False
        with self._lock:
            return self._active_polls > 0 or time.time() - self._last_poll_end < ONLINE_GRACE_S

    def penalize(self, seconds=60):
        """Pekerja gagal/terlambat: pakai Edge dulu sebentar supaya Budi tidak menunggu berulang kali."""
        self._penalty_until = time.time() + seconds

    def submit(self, text) -> _Job:
        job = _Job(text)
        with self._lock:
            self._gc()
            self._jobs[job.id] = job
            self._queue.append(job)
        return job

    def cancel(self, job):
        job.cancelled = True
        job.done.set()

    def _gc(self):
        now = time.time()
        for jid in [j for j, job in self._jobs.items() if now - job.created > JOB_TTL_S]:
            self._jobs.pop(jid, None)
        while self._queue and (self._queue[0].cancelled or now - self._queue[0].created > JOB_TTL_S):
            self._queue.popleft()

    # ---------- dipakai handler pekerja ----------
    def _take_nowait(self):
        with self._lock:
            while self._queue:
                job = self._queue.popleft()
                if not job.cancelled:
                    job.taken_at = time.time()
                    return job
        return None

    async def take(self, wait_s: float):
        with self._lock:
            self._active_polls += 1
            self._last_seen = time.time()
        try:
            deadline = time.monotonic() + wait_s
            while True:
                job = self._take_nowait()
                if job or time.monotonic() >= deadline:
                    return job
                await asyncio.sleep(0.05)
        finally:
            with self._lock:
                self._active_polls -= 1
                self._last_poll_end = self._last_seen = time.time()

    def complete(self, job_id, audio=None, error=None) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if not job:
            return False
        job.audio, job.error = audio, error
        if audio:
            self.stats["served"] += 1
            self.stats["last_latency"] = round(time.time() - (job.taken_at or job.created), 2)
        else:
            self.stats["failed"] += 1
        job.done.set()
        return True

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "online": self.online(),
            "last_seen_s": round(time.time() - self._last_seen, 1) if self._last_seen else None,
            "queue": len(self._queue),
            "worker": self.worker,
            **self.stats,
        }

    # ---------- HTTP ----------
    def _authorized(self, request) -> bool:
        tok = request.headers.get("X-Worker-Token", "")
        return self.enabled and bool(tok) and hmac.compare_digest(tok.encode(), self.token.encode())

    async def _deny(self):
        await asyncio.sleep(1.0)
        return web.json_response({"error": "Unauthorized"}, status=401)

    async def handle_next(self, request):
        if not self._authorized(request):
            return await self._deny()
        info = {k: request.query[k] for k in ("voice", "gpu", "version") if k in request.query}
        if info:
            self.worker = info
        try:
            wait_s = min(float(request.query.get("wait", MAX_POLL_S)), MAX_POLL_S)
        except ValueError:
            wait_s = MAX_POLL_S
        job = await self.take(max(wait_s, 0.0))
        if not job:
            return web.Response(status=204)
        return web.json_response({"id": job.id, "text": job.text})

    async def handle_result(self, request):
        if not self._authorized(request):
            return await self._deny()
        job_id = request.match_info["job_id"]
        err = request.headers.get("X-Error")
        if err:
            ok = self.complete(job_id, error=err[:300])
        else:
            if (request.content_length or 0) > MAX_AUDIO_BYTES:
                return web.json_response({"error": "too large"}, status=413)
            data = await request.read()
            if len(data) > MAX_AUDIO_BYTES or len(data) < 64:
                return web.json_response({"error": "bad audio"}, status=400)
            rtf = request.headers.get("X-Rtf")
            if rtf:
                try:
                    self.stats["last_rtf"] = round(float(rtf), 3)
                except ValueError:
                    pass
            ok = self.complete(job_id, audio=data)
        return web.json_response({"ok": ok}, status=200 if ok else 410)

    async def handle_package(self, request):
        """Paket suara (LoRA + klip patokan) untuk sesi GPU baru, supaya tidak perlu unggah ulang."""
        if not self._authorized(request):
            return await self._deny()
        path = os.path.join(self.base_dir, "data", "voice", "budi_voice.zip")
        if not os.path.isfile(path):
            return web.json_response({"error": "Paket suara belum ada di STB"}, status=404)
        return web.FileResponse(path, headers={"Content-Type": "application/zip", "Cache-Control": "no-store"})

    async def handle_status(self, request):
        return web.json_response(self.status(), headers={"Cache-Control": "no-store"})

    def register_routes(self, app):
        app.router.add_get(WORKER_PREFIX + "next", self.handle_next)
        app.router.add_post(WORKER_PREFIX + "result/{job_id}", self.handle_result)
        app.router.add_get(WORKER_PREFIX + "package", self.handle_package)
        app.router.add_get("/api/voice/status", self.handle_status)


BROKER = VoiceBroker()
