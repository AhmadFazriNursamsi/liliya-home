"""Unduh video dari link (TikTok tanpa watermark, juga YouTube, Instagram, Facebook, X, dll.).

Link dikirim dari tab Unduh di dashboard atau lewat "Bagikan → Liliya" di APK. STB mengambil videonya
dengan yt-dlp; khusus TikTok, bila yt-dlp gagal (TikTok sering berubah) dipakai cadangan API TikWM.

Penyimpanan sementara: data/unduh/<id>/{meta.json, video.<ext>}. File dihapus begitu selesai diunduh
ke HP/laptop; yang tidak pernah diambil dihapus setelah `keep_minutes`.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import time
import unicodedata
from urllib.parse import quote, urlparse

import aiohttp
from aiohttp import web

ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
TIKTOK_HOSTS = ("tiktok.com", "tiktokv.com", "douyin.com")
TIKWM_API = "https://www.tikwm.com/api/"
MIME = {"mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska", "mov": "video/quicktime",
        "m4a": "audio/mp4", "mp3": "audio/mpeg"}


def _is_tiktok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in TIKTOK_HOSTS)


def _clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name or "").strip()
    name = "".join(c for c in name if unicodedata.category(c)[0] not in "CS" and c not in '/\\:*?"<>|#')
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:80].rstrip(" .") or "video"


class UnduhManager:
    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("unduh", {}) or {}
        self.enabled = cfg.get("enabled", True)
        self.root = os.path.join(base_dir, "data", "unduh")
        self.max_bytes = int(cfg.get("max_size_mb", 500)) * 1024 * 1024
        self.keep_seconds = int(cfg.get("keep_minutes", 60)) * 60
        self.timeout = int(cfg.get("timeout_sec", 600))
        self.tikwm = cfg.get("tikwm_fallback", True)
        self.ytdlp = cfg.get("yt_dlp") or shutil.which("yt-dlp")
        self._sem = None           # dibuat di event loop server (lihat _on_startup)
        self._tasks = {}
        os.makedirs(self.root, exist_ok=True)
        if self.enabled and not self.ytdlp:
            print("[Unduh Warning] yt-dlp tidak ditemukan: hanya TikTok (lewat TikWM) yang bisa diunduh.")

    # ---------- Rute ----------
    def register_routes(self, app: web.Application):
        if not self.enabled:
            return
        app.router.add_get("/api/unduh", self.handle_list)
        app.router.add_post("/api/unduh", self.handle_create)
        app.router.add_get("/api/unduh/{id}", self.handle_meta)
        app.router.add_delete("/api/unduh/{id}", self.handle_delete)
        app.router.add_get("/api/unduh/{id}/file", self.handle_file)
        app.on_startup.append(self._on_startup)

    async def _on_startup(self, app):
        self._sem = asyncio.Semaphore(2)
        # Unduhan yang terputus karena layanan dimulai ulang tidak dilanjutkan: tandai gagal
        for meta in self._all_meta():
            if meta.get("status") == "working":
                meta.update(status="error", error="Terputus karena Liliya dimulai ulang. Coba lagi.")
                self._save(meta)
        asyncio.get_running_loop().create_task(self._cleanup_loop())

    # ---------- Penyimpanan ----------
    def _dir(self, job_id: str) -> str:
        return os.path.join(self.root, job_id)

    def _load(self, job_id: str):
        if not ID_RE.match(job_id or ""):
            return None
        try:
            with open(os.path.join(self._dir(job_id), "meta.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, meta: dict):
        path = os.path.join(self._dir(meta["id"]), "meta.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _all_meta(self):
        out = []
        for name in os.listdir(self.root):
            meta = self._load(name)
            if meta:
                out.append(meta)
        return out

    def _get_or_404(self, request) -> dict:
        meta = self._load(request.match_info["id"])
        if not meta:
            raise web.HTTPNotFound(text=json.dumps({"error": "Video tidak ditemukan (sudah diunduh atau kedaluwarsa)"}),
                                   content_type="application/json")
        return meta

    @staticmethod
    def _public(meta: dict) -> dict:
        return {k: meta.get(k) for k in ("id", "url", "status", "error", "title", "author", "site", "ext",
                                          "size", "hd", "via", "created")}

    def _delete(self, job_id: str):
        task = self._tasks.pop(job_id, None)
        if task:
            task.cancel()
        shutil.rmtree(self._dir(job_id), ignore_errors=True)

    # ---------- API ----------
    async def handle_list(self, request):
        jobs = sorted(self._all_meta(), key=lambda m: m.get("created", 0), reverse=True)
        return web.json_response({"jobs": [self._public(m) for m in jobs],
                                  "keep_minutes": self.keep_seconds // 60},
                                 headers={"Cache-Control": "no-store"})

    async def handle_meta(self, request):
        return web.json_response(self._public(self._get_or_404(request)), headers={"Cache-Control": "no-store"})

    async def handle_create(self, request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        # Teks bagikan TikTok berisi kalimat + link: ambil link pertama saja
        match = URL_RE.search(str(data.get("url") or ""))
        if not match:
            return web.json_response({"error": "Tidak ada link video. Tempel link dari TikTok, YouTube, Instagram, dll."},
                                     status=400)
        url = match.group(0).rstrip(".,)")
        if len(url) > 2000:
            return web.json_response({"error": "Link terlalu panjang"}, status=400)
        if not self.ytdlp and not _is_tiktok(url):
            return web.json_response({"error": "yt-dlp belum terpasang di STB, jadi baru link TikTok yang bisa diunduh."},
                                     status=503)

        job_id = secrets.token_urlsafe(12)
        os.makedirs(self._dir(job_id))
        meta = {"id": job_id, "url": url, "hd": bool(data.get("hd")), "status": "working", "error": None,
                "title": None, "author": None, "site": "TikTok" if _is_tiktok(url) else urlparse(url).hostname,
                "ext": None, "size": None, "via": None, "file": None, "created": int(time.time())}
        self._save(meta)
        task = asyncio.get_running_loop().create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        print(f"[Unduh] ⬇️ {url}")
        return web.json_response(self._public(meta), status=201)

    async def handle_delete(self, request):
        meta = self._get_or_404(request)
        self._delete(meta["id"])
        return web.json_response({"status": "success"})

    async def handle_file(self, request):
        meta = self._get_or_404(request)
        path = os.path.join(self._dir(meta["id"]), meta.get("file") or "")
        if meta.get("status") != "ready" or not meta.get("file") or not os.path.exists(path):
            return web.json_response({"error": "Video belum siap"}, status=409)
        size = os.path.getsize(path)
        name = f"{_clean_name(meta.get('title') or meta.get('author') or 'video')}.{meta['ext']}"
        # Nama ASCII untuk Android lama (URLUtil.guessFileName tidak membaca filename*)
        ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "", name.encode("ascii", "ignore").decode()).strip(" .")
        if len(ascii_name) <= len(meta["ext"]) + 1:
            ascii_name = f"video-{meta['id'][:8]}.{meta['ext']}"
        resp = web.StreamResponse(headers={
            "Content-Type": MIME.get(meta["ext"], "application/octet-stream"),
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        })
        await resp.prepare(request)
        if request.method == "HEAD":
            return resp
        with open(path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                await resp.write(chunk)        # klien memutus di tengah jalan → exception, file tetap ada
        await resp.write_eof()
        # Terkirim utuh: hapus dari STB (WebView APK membatalkan permintaan pertamanya sebelum selesai,
        # baru DownloadManager mengunduh penuh — jadi hanya unduhan yang selesai yang menghapus)
        self._delete(meta["id"])
        print(f"[Unduh] ✅ Terkirim & dihapus: {name}")
        return resp

    # ---------- Proses ----------
    async def _run(self, job_id: str):
        async with self._sem:
            meta = self._load(job_id)
            if not meta:
                return
            errors = []
            result = None
            if self.ytdlp:
                result, err = await self._via_ytdlp(meta)
                if err:
                    errors.append(err)
            if not result and self.tikwm and _is_tiktok(meta["url"]):
                result, err = await self._via_tikwm(meta)
                if err:
                    errors.append(err)
            meta = self._load(job_id)
            if not meta:              # dihapus saat sedang diunduh
                return
            if result:
                meta.update(result, status="ready", error=None)
                print(f"[Unduh] 🎬 Siap ({meta['via']}): {meta.get('title')}")
            else:
                meta.update(status="error", error=errors[-1] if errors else "Video tidak bisa diambil.")
                print(f"[Unduh] ❌ Gagal: {meta['url']} — {' | '.join(errors)}")
                for f in os.listdir(self._dir(job_id)):        # sisa unduhan setengah jadi
                    if f != "meta.json":
                        os.unlink(os.path.join(self._dir(job_id), f))
            self._save(meta)

    async def _via_ytdlp(self, meta: dict):
        folder = self._dir(meta["id"])
        height = 1080 if meta["hd"] else 720
        if _is_tiktok(meta["url"]):
            # Tanpa watermark; H.264 diutamakan supaya bisa diputar & dibagikan ulang di semua HP
            fmt = ("best[format_note!*=watermark][vcodec^=h264]/best[format_note!*=watermark]"
                   if not meta["hd"] else "best[format_note!*=watermark]")
        else:
            fmt = (f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/b[height<={height}][ext=mp4]"
                   f"/bv*[height<={height}]+ba/b[height<={height}]/b")
        cmd = [self.ytdlp, "--ignore-config", "--no-playlist", "--no-progress", "--no-warnings",
               "--use-extractors", "default,-generic",      # hanya situs yang dikenal, bukan sembarang URL
               "--max-filesize", str(self.max_bytes), "--merge-output-format", "mp4",
               "-f", fmt, "-o", os.path.join(folder, "video.%(ext)s"),
               "-O", "after_move:%(.{title,uploader,channel,extractor_key,filepath})j", "--", meta["url"]]
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, start_new_session=True)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return None, f"Terlalu lama (lebih dari {self.timeout // 60} menit)."
        except asyncio.CancelledError:
            proc.kill()
            raise
        if proc.returncode != 0:
            text = err.decode("utf-8", "replace").strip().splitlines()
            line = next((l for l in reversed(text) if l.startswith("ERROR")), text[-1] if text else "")
            print(f"[Unduh] yt-dlp keluar {proc.returncode}: {line[-300:]}")
            if "Unsupported URL" in line:
                return None, "Situs ini belum didukung."
            if "Private" in line or "login" in line.lower():
                return None, "Video ini privat atau perlu login."
            if "File is larger" in line:
                return None, f"Video terlalu besar (maksimal {self.max_bytes // 1048576} MB)."
            return None, "Video tidak bisa diambil. Pastikan link benar dan videonya publik."
        try:
            info = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None, "yt-dlp tidak mengembalikan file."
        path = info.get("filepath") or ""
        if not os.path.isfile(path) or os.path.dirname(os.path.abspath(path)) != os.path.abspath(folder):
            return None, "yt-dlp tidak mengembalikan file."
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        return {"file": os.path.basename(path), "ext": ext, "size": os.path.getsize(path), "via": "yt-dlp",
                "title": info.get("title"), "author": info.get("uploader") or info.get("channel"),
                "site": info.get("extractor_key") or meta["site"]}, None

    async def _via_tikwm(self, meta: dict):
        timeout = aiohttp.ClientTimeout(total=self.timeout, sock_connect=15, sock_read=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(TIKWM_API, data={"url": meta["url"], "hd": "1" if meta["hd"] else "0"}) as r:
                    body = await r.json(content_type=None)
                if body.get("code") != 0 or not isinstance(body.get("data"), dict):
                    print(f"[Unduh] TikWM: {body.get('msg')}")
                    return None, "Video TikTok tidak bisa diambil (privat, dihapus, atau layanan cadangan sedang sibuk)."
                d = body["data"]
                if d.get("images"):
                    return None, "Postingan foto (slide) belum didukung, hanya video."
                # play = H.264 tanpa watermark (aman di semua HP); hdplay = HEVC 720p+, lebih tajam
                src = (d.get("hdplay") if meta["hd"] else None) or d.get("play")
                if not src:
                    return None, "Layanan cadangan tidak memberi link video."
                if src.startswith("/"):
                    src = "https://www.tikwm.com" + src
                if urlparse(src).scheme != "https":
                    return None, "Link video dari layanan cadangan tidak valid."
                path = os.path.join(self._dir(meta["id"]), "video.mp4")
                size = 0
                async with session.get(src) as r:
                    if r.status != 200:
                        return None, f"Gagal mengambil file video (kode {r.status})."
                    with open(path, "wb") as f:
                        async for chunk in r.content.iter_chunked(256 * 1024):
                            size += len(chunk)
                            if size > self.max_bytes:
                                return None, f"Video terlalu besar (maksimal {self.max_bytes // 1048576} MB)."
                            f.write(chunk)
                if size < 1024:
                    return None, "File video dari layanan cadangan kosong."
                author = d.get("author") or {}
                return {"file": "video.mp4", "ext": "mp4", "size": size, "via": "tikwm", "site": "TikTok",
                        "title": d.get("title"), "author": author.get("unique_id") or author.get("nickname")}, None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            print(f"[Unduh] TikWM error: {e!r}")
            return None, "Layanan cadangan TikTok tidak terjangkau. Coba lagi nanti."

    # ---------- Pembersihan ----------
    async def _cleanup_loop(self):
        while True:
            try:
                cutoff = time.time() - self.keep_seconds
                for meta in self._all_meta():
                    if meta.get("created", 0) < cutoff and meta["id"] not in self._tasks:
                        self._delete(meta["id"])
                        print(f"[Unduh] 🧹 Dihapus (tidak diambil {self.keep_seconds // 60} menit): {meta.get('title') or meta['url']}")
            except Exception as e:
                print(f"[Unduh Warning] Pembersihan gagal: {e}")
            await asyncio.sleep(300)
