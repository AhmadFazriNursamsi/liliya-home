"""Penampil dokumen Office tanpa iklan.

Dokumen (Word, Excel, PowerPoint, OpenDocument, RTF, CSV, PDF) dikirim dari APK Liliya
("Buka dengan" / "Bagikan" dari WhatsApp) atau dari tab Dokumen di dashboard, diubah ke PDF oleh
LibreOffice tanpa GUI di STB, lalu ditampilkan dengan pdf.js (web/docs.html).
Markdown, teks, dan kode tidak dikonversi: isinya dikirim apa adanya (/api/docs/{id}/text) dan
ditampilkan langsung di penampil, supaya emoji dan huruf apa pun tampil seperti di HP.
Arsip ZIP/RAR/7z (lewat lsar/unar dari paket "unar") ditampilkan sebagai daftar isi; file di dalamnya
dikeluarkan satu per satu ke stdout (tidak pernah dibongkar ke disk) lalu dibuka sebagai dokumen baru.

Penyimpanan: data/docs/<id>/{meta.json, source.<ext>, view.pdf}. File yang isinya sama (sha256)
tidak dikonversi ulang. Dokumen yang tidak dibuka selama `retention_days` dihapus otomatis.
"""

import asyncio
import hashlib
import json
import mimetypes
import subprocess
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
import unicodedata
from urllib.parse import quote, unquote

from aiohttp import web

# pdf.js dimuat sebagai ES module: browser menolak .mjs yang dikirim sebagai application/octet-stream
mimetypes.add_type("text/javascript", ".mjs")

WRITER = {"doc", "docx", "docm", "dot", "dotx", "dotm", "odt", "ott", "fodt", "rtf", "wpd", "wps", "pages"}
CALC = {"xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "ods", "ots", "fods", "csv", "tsv", "numbers"}
IMPRESS = {"ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "odp", "otp", "fodp", "key"}
MARKDOWN = {"md", "markdown", "mdown", "mkd"}
TEXT = {"txt", "log", "json", "xml", "yaml", "yml", "ini", "conf", "cfg", "env", "srt", "vtt", "html", "htm",
        "py", "js", "ts", "java", "kt", "c", "cpp", "h", "cs", "php", "go", "rs", "sh", "bat", "sql", "css"}
ARCHIVE = {"zip", "rar", "7z"}
SUPPORTED = WRITER | CALC | IMPRESS | MARKDOWN | TEXT | ARCHIVE | {"pdf"}
NO_CONVERT = MARKDOWN | TEXT | ARCHIVE | {"pdf"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}   # tanpa SVG: bisa berisi skrip
MAX_ARCHIVE_ENTRIES = 3000
ARCHIVE_PASSWORD_TTL = 1800
MAX_TEXT_BYTES = 5 * 1024 * 1024

# Dipakai bila nama file tidak berekstensi (sebagian aplikasi mengirim nama tanpa ekstensi)
MIME_EXT = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template": "dotx",
    "application/vnd.ms-word.document.macroenabled.12": "docm",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": "xlsm",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12": "xlsb",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow": "ppsx",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12": "pptm",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "text/csv": "csv",
    "text/comma-separated-values": "csv",
    "text/tab-separated-values": "tsv",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "application/json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/html": "html",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/vnd.rar": "rar",
    "application/x-rar-compressed": "rar",
    "application/x-rar": "rar",
    "application/x-7z-compressed": "7z",
}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")


def _kind(ext: str) -> str:
    if ext in WRITER:
        return "word"
    if ext in CALC:
        return "sheet"
    if ext in IMPRESS:
        return "slides"
    if ext in MARKDOWN:
        return "markdown"
    if ext in TEXT:
        return "text"
    if ext in ARCHIVE:
        return "archive"
    return "pdf"


def _clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename(name or "").strip())
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C" and c not in '/\\:*?"<>|')
    return name[:150] or "dokumen"


def _sniff_ext(head: bytes) -> str:
    """Tebakan terakhir dari isi file bila nama & tipe MIME tidak membantu."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):      # OLE2: .doc/.xls/.ppt lama, LibreOffice mengenali sendiri
        return "doc"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"PK\x03\x04"):
        if b"word/" in head:
            return "docx"
        if b"xl/" in head:
            return "xlsx"
        if b"ppt/" in head:
            return "pptx"
        return "zip"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    # Teks biasa: tanpa karakter kontrol selain tab/baris baru (file biner hampir selalu punya)
    if head and not any(b < 32 and b not in (9, 10, 12, 13) for b in head):
        try:
            head.decode("utf-8")
            return "txt"
        except UnicodeDecodeError as e:
            if e.start >= len(head) - 4:    # karakter multibyte terpotong di ujung potongan 4 KB
                return "txt"
    return ""


class DocsManager:
    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("docs", {}) or {}
        self.enabled = cfg.get("enabled", True)
        self.root = os.path.join(base_dir, "data", "docs")
        self.profile = os.path.join(self.root, ".lo-profile")
        self.viewer_page = os.path.join(base_dir, "web", "docs.html")
        self.max_bytes = int(cfg.get("max_upload_mb", 50)) * 1024 * 1024
        self.retention_days = int(cfg.get("retention_days", 30))
        self.max_total_bytes = int(cfg.get("max_storage_mb", 3072)) * 1024 * 1024
        self.timeout = int(cfg.get("convert_timeout_sec", 300))
        self.soffice = cfg.get("soffice") or shutil.which("soffice") or shutil.which("libreoffice")
        self.lsar, self.unar = shutil.which("lsar"), shutil.which("unar")
        self._archive_pw = {}       # doc_id -> (kata sandi, kedaluwarsa); hanya di memori
        self._lock = None          # dibuat di event loop server (lihat _on_startup)
        self._tasks = {}
        os.makedirs(self.root, exist_ok=True)
        if self.enabled and not self.soffice:
            print("[Docs Warning] LibreOffice (soffice) tidak ditemukan: hanya PDF yang bisa dibuka. "
                  "Pasang: sudo apt install libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui")

    # ---------- Rute ----------
    def register_routes(self, app: web.Application):
        if not self.enabled:
            return
        app.router.add_get("/api/docs", self.handle_list)
        app.router.add_post("/api/docs", self.handle_upload)
        app.router.add_get("/api/docs/{id}", self.handle_meta)
        app.router.add_delete("/api/docs/{id}", self.handle_delete)
        app.router.add_post("/api/docs/{id}/retry", self.handle_retry)
        app.router.add_get("/api/docs/{id}/pdf", self.handle_pdf)
        app.router.add_get("/api/docs/{id}/original", self.handle_original)
        app.router.add_get("/api/docs/{id}/text", self.handle_text)
        app.router.add_get("/api/docs/{id}/entries", self.handle_entries)
        app.router.add_post("/api/docs/{id}/password", self.handle_password)
        app.router.add_post("/api/docs/{id}/entries/{index}/open", self.handle_entry_open)
        app.router.add_get("/api/docs/{id}/entries/{index}/raw", self.handle_entry_raw)
        app.router.add_get("/docs/login-ok", self.handle_login_ok)
        app.router.add_get("/docs/{id}", self.handle_viewer)
        app.on_startup.append(self._on_startup)

    async def _on_startup(self, app):
        self._lock = asyncio.Lock()
        # Konversi yang terputus karena layanan dimulai ulang: lanjutkan
        for meta in self._all_meta():
            if meta.get("status") == "converting":
                self._schedule(meta["id"])
        asyncio.get_running_loop().create_task(self._cleanup_loop())

    # ---------- Penyimpanan ----------
    def _dir(self, doc_id: str) -> str:
        return os.path.join(self.root, doc_id)

    def _meta_path(self, doc_id: str) -> str:
        return os.path.join(self._dir(doc_id), "meta.json")

    def _load(self, doc_id: str):
        if not ID_RE.match(doc_id or ""):
            return None
        try:
            with open(self._meta_path(doc_id), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, meta: dict):
        path = self._meta_path(meta["id"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _all_meta(self):
        out = []
        for name in os.listdir(self.root):
            if name.startswith("."):
                continue
            meta = self._load(name)
            if meta:
                out.append(meta)
        return out

    def _get_or_404(self, request) -> dict:
        meta = self._load(request.match_info["id"])
        if not meta:
            raise web.HTTPNotFound(text=json.dumps({"error": "Dokumen tidak ditemukan (mungkin sudah terhapus)"}),
                                   content_type="application/json")
        return meta

    @staticmethod
    def _public(meta: dict) -> dict:
        return {k: meta.get(k) for k in ("id", "name", "ext", "kind", "size", "status", "error",
                                          "created", "opened", "convert_seconds")}

    def _pdf_path(self, meta: dict) -> str:
        return os.path.join(self._dir(meta["id"]), "source.pdf" if meta["ext"] == "pdf" else "view.pdf")

    def _delete_dir(self, doc_id: str):
        shutil.rmtree(self._dir(doc_id), ignore_errors=True)

    # ---------- API ----------
    async def handle_list(self, request):
        docs = sorted(self._all_meta(), key=lambda m: m.get("opened", 0), reverse=True)
        return web.json_response({"docs": [self._public(m) for m in docs],
                                  "office": bool(self.soffice), "max_mb": self.max_bytes // (1024 * 1024)})

    async def handle_meta(self, request):
        meta = self._get_or_404(request)
        now = int(time.time())
        if now - meta.get("opened", 0) > 60:     # penampil memanggil ini berulang saat menunggu konversi
            meta["opened"] = now
            self._save(meta)
        return web.json_response(self._public(meta), headers={"Cache-Control": "no-store"})

    async def handle_upload(self, request):
        name = _clean_name(unquote(request.headers.get("X-Filename", "")) or request.query.get("name", ""))
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        if ext not in SUPPORTED:
            mime = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = MIME_EXT.get(mime, "") if ext == "" or mime in MIME_EXT else ext

        length = request.content_length
        if length is not None and length > self.max_bytes:
            return self._too_big()

        tmp = os.path.join(self.root, f".upload-{secrets.token_hex(8)}")
        sha = hashlib.sha256()
        size = 0
        head = b""
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("too_big")
                    if len(head) < 4096:
                        head += chunk[:4096 - len(head)]
                    sha.update(chunk)
                    f.write(chunk)
        except ValueError:
            os.unlink(tmp)
            return self._too_big()
        except Exception as e:
            if os.path.exists(tmp):
                os.unlink(tmp)
            return web.json_response({"error": f"Unggahan terputus: {e}"}, status=400)
        return self._ingest(tmp, name, ext, size, head, sha.hexdigest())

    def _ingest(self, tmp: str, name: str, ext: str, size: int, head: bytes, digest: str):
        """File sementara `tmp` menjadi dokumen (atau dipakai ulang bila isinya sudah pernah dibuka)."""
        if size == 0:
            os.unlink(tmp)
            return web.json_response({"error": "File kosong"}, status=400)
        if ext not in SUPPORTED:
            sniffed = _sniff_ext(head)
            # Tebakan "txt"/arsip hanya untuk file tanpa ekstensi: .apk/.jar (isinya ZIP) dsb. tetap ditolak
            if sniffed and (sniffed not in {"txt"} | ARCHIVE or not ext):
                ext = sniffed
        if ext not in SUPPORTED:
            os.unlink(tmp)
            label = f".{ext}" if ext else "ini"
            return web.json_response({"error": f"Jenis file {label} belum didukung. Yang bisa dibuka: "
                                               "Word, Excel, PowerPoint, OpenDocument, RTF, CSV, PDF, "
                                               "Markdown, teks, kode, ZIP, RAR, dan 7z."},
                                     status=415)
        if ext in TEXT | MARKDOWN and size > MAX_TEXT_BYTES:
            os.unlink(tmp)
            return web.json_response({"error": "File teks terlalu besar untuk ditampilkan (maksimal 5 MB)"}, status=413)
        if ext not in NO_CONVERT and not self.soffice:
            os.unlink(tmp)
            return web.json_response({"error": "LibreOffice belum terpasang di STB, jadi baru PDF yang bisa dibuka."},
                                     status=503)
        if not os.path.splitext(name)[1]:
            name = f"{name}.{ext}"

        now = int(time.time())
        # Isi sama sudah pernah dibuka: pakai hasil lama (langsung tampil), cukup perbarui nama & waktu
        for meta in self._all_meta():
            if meta.get("sha256") == digest and meta.get("status") != "error":
                os.unlink(tmp)
                meta.update(name=name, opened=now)
                self._save(meta)
                return web.json_response(self._public(meta))

        doc_id = secrets.token_urlsafe(12)
        os.makedirs(self._dir(doc_id))
        os.replace(tmp, os.path.join(self._dir(doc_id), f"source.{ext}"))
        meta = {"id": doc_id, "name": name, "ext": ext, "kind": _kind(ext), "size": size, "sha256": digest,
                "created": now, "opened": now, "status": "converting", "error": None}
        if ext in NO_CONVERT:
            meta["status"] = "ready"        # PDF, Markdown, teks & arsip ditampilkan apa adanya, tanpa konversi
        self._save(meta)
        if meta["status"] == "converting":
            self._schedule(doc_id)
        self._enforce_quota(keep=doc_id)
        print(f"[Docs] 📄 Diterima: {name} ({size // 1024} KB)")
        return web.json_response(self._public(meta), status=201)

    def _too_big(self):
        mb = self.max_bytes // (1024 * 1024)
        return web.json_response({"error": f"File terlalu besar (maksimal {mb} MB)"}, status=413)

    async def handle_delete(self, request):
        meta = self._get_or_404(request)
        task = self._tasks.pop(meta["id"], None)
        if task:
            task.cancel()
        self._delete_dir(meta["id"])
        return web.json_response({"status": "success"})

    async def handle_retry(self, request):
        meta = self._get_or_404(request)
        if meta.get("status") == "error":
            meta.update(status="converting", error=None)
            self._save(meta)
            self._schedule(meta["id"])
        return web.json_response(self._public(meta))

    async def handle_pdf(self, request):
        meta = self._get_or_404(request)
        path = self._pdf_path(meta)
        if meta.get("status") != "ready" or not os.path.exists(path):
            return web.json_response({"error": "Dokumen belum siap"}, status=409)
        # pdf.js mengambil per bagian (Range); id unik per isi, jadi aman di-cache browser
        return web.FileResponse(path, headers={"Content-Type": "application/pdf",
                                               "Cache-Control": "private, max-age=604800"})

    async def handle_text(self, request):
        meta = self._get_or_404(request)
        if meta.get("kind") not in ("markdown", "text"):
            return web.json_response({"error": "Bukan dokumen teks"}, status=409)
        path = os.path.join(self._dir(meta["id"]), f"source.{meta['ext']}")
        if not os.path.exists(path):
            raise web.HTTPNotFound()
        with open(path, "rb") as f:
            raw = f.read(MAX_TEXT_BYTES)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")   # file lama dari Windows
        # Selalu text/plain: HTML/JS di dalam file tidak pernah dijalankan browser
        return web.Response(text=text, content_type="text/plain", charset="utf-8",
                            headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"})

    # ---------- Arsip ZIP / RAR / 7z ----------
    def _archive_path(self, request) -> tuple:
        meta = self._get_or_404(request)
        if meta.get("kind") != "archive":
            raise web.HTTPConflict(text=json.dumps({"error": "Bukan arsip"}), content_type="application/json")
        if not (self.lsar and self.unar):
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "Pembuka arsip (unar) belum terpasang di STB"}),
                                             content_type="application/json")
        return meta, os.path.join(self._dir(meta["id"]), f"source.{meta['ext']}")

    def _password(self, doc_id: str) -> str:
        pw, until = self._archive_pw.get(doc_id, ("", 0))
        return pw if until > time.time() else ""

    def _list_archive(self, path: str, password: str) -> dict:
        cmd = [self.lsar, "-j", "-nr"] + (["-p", password] if password else []) + ["--", path]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        try:
            data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        except ValueError:
            err = proc.stderr.decode("utf-8", errors="replace") + proc.stdout.decode("utf-8", errors="replace")
            if "password" in err.lower():
                return {"need_password": True, "wrong": bool(password)}
            raise RuntimeError("Arsip rusak atau formatnya tidak dikenali")
        props = data.get("lsarProperties") or {}
        entries = []
        for e in data.get("lsarContents") or []:
            if e.get("XADIsDirectory") or e.get("XADIsLink"):
                continue
            entries.append({"i": e.get("XADIndex"), "name": e.get("XADFileName") or "?",
                            "size": e.get("XADFileSize") or 0, "enc": bool(e.get("XADIsEncrypted"))})
            if len(entries) >= MAX_ARCHIVE_ENTRIES:
                break
        return {"format": data.get("lsarFormatName", ""), "entries": entries,
                "encrypted": bool(props.get("XADIsEncrypted")) or any(x["enc"] for x in entries)}

    def _extract_entry(self, path: str, entry: dict, password: str, dest: str) -> tuple:
        """Keluarkan satu file ke `dest` lewat stdout unar (nama di dalam arsip tidak pernah dipakai sebagai path).
        unar mode stdout keluar dengan kode 0 walau kata sandi salah (isinya kosong), jadi ukuran hasil
        dibandingkan dengan ukuran yang tercatat di arsip."""
        index = entry["i"]
        cmd = [self.unar, "-q", "-nr", "-o", "-", "-i"] + (["-p", password] if password else []) + ["--", path, str(index)]
        sha, size, head = hashlib.sha256(), 0, b""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = proc.stdout.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_bytes:
                        proc.kill()
                        raise ValueError("too_big")
                    if len(head) < 4096:
                        head += chunk[:4096 - len(head)]
                    sha.update(chunk)
                    f.write(chunk)
            err = proc.stderr.read().decode("utf-8", errors="replace")
            code = proc.wait(timeout=120)
        except Exception:
            proc.kill()
            raise
        if code != 0:
            raise PermissionError("password") if "password" in err.lower() else RuntimeError("Gagal mengeluarkan file dari arsip")
        if size != entry.get("size", size):
            if entry.get("enc"):
                raise PermissionError("password")
            raise RuntimeError("Isi arsip rusak atau tidak lengkap")
        return size, head, sha.hexdigest()

    async def _entry(self, request) -> tuple:
        meta, path = self._archive_path(request)
        try:
            index = int(request.match_info["index"])
        except ValueError:
            raise web.HTTPNotFound()
        listing = await asyncio.get_running_loop().run_in_executor(None, self._list_archive, path, self._password(meta["id"]))
        entry = next((e for e in listing.get("entries", []) if e["i"] == index), None)
        if not entry:
            raise web.HTTPNotFound(text=json.dumps({"error": "File tidak ada di arsip"}), content_type="application/json")
        return meta, path, entry

    async def handle_entries(self, request):
        meta, path = self._archive_path(request)
        try:
            listing = await asyncio.get_running_loop().run_in_executor(None, self._list_archive, path, self._password(meta["id"]))
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return web.json_response({"error": str(e) or "Arsip tidak bisa dibaca"}, status=422)
        listing["unlocked"] = bool(self._password(meta["id"]))
        return web.json_response(listing, headers={"Cache-Control": "no-store"})

    async def handle_password(self, request):
        meta, path = self._archive_path(request)
        body = await request.json()
        password = str((body or {}).get("password", ""))[:256]
        if not password:
            return web.json_response({"error": "Kata sandi kosong"}, status=400)
        loop = asyncio.get_running_loop()
        listing = await loop.run_in_executor(None, self._list_archive, path, password)
        if listing.get("need_password"):
            return web.json_response({"error": "Kata sandi salah"}, status=403)
        # Uji dengan mengeluarkan file terkecil yang terkunci
        locked = sorted((e for e in listing["entries"] if e["enc"]), key=lambda e: e["size"])
        if locked:
            tmp = os.path.join(self.root, f".pwtest-{secrets.token_hex(6)}")
            try:
                await loop.run_in_executor(None, self._extract_entry, path, locked[0], password, tmp)
            except PermissionError:
                return web.json_response({"error": "Kata sandi salah"}, status=403)
            except Exception:
                pass    # file rusak/terlalu besar: kata sandi tetap dianggap benar
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        self._archive_pw[meta["id"]] = (password, time.time() + ARCHIVE_PASSWORD_TTL)
        return web.json_response({"status": "ok"})

    async def handle_entry_open(self, request):
        meta, path, entry = await self._entry(request)
        name = _clean_name(entry["name"].replace("\\", "/").rsplit("/", 1)[-1])
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        raw_url = f"/api/docs/{meta['id']}/entries/{entry['i']}/raw"
        if ext in IMAGE_EXT:
            return web.json_response({"image": True, "raw": raw_url, "name": name})
        if ext not in SUPPORTED and ext:
            return web.json_response({"error": f"File .{ext} belum bisa dibuka di Liliya, tapi bisa diunduh.",
                                      "download": raw_url, "name": name}, status=415)
        tmp = os.path.join(self.root, f".upload-{secrets.token_hex(8)}")
        try:
            size, head, digest = await asyncio.get_running_loop().run_in_executor(
                None, self._extract_entry, path, entry, self._password(meta["id"]), tmp)
        except PermissionError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            return web.json_response({"error": "Arsip ini dikunci kata sandi", "need_password": True}, status=403)
        except ValueError:
            os.unlink(tmp)
            return self._too_big()
        except Exception as e:
            if os.path.exists(tmp):
                os.unlink(tmp)
            return web.json_response({"error": str(e) or "Gagal membuka file"}, status=422)
        resp = self._ingest(tmp, name, ext, size, head, digest)
        if resp.status == 415:
            return web.json_response({"error": "File ini belum bisa dibuka di Liliya, tapi bisa diunduh.",
                                      "download": raw_url, "name": name}, status=415)
        return resp

    async def handle_entry_raw(self, request):
        meta, path, entry = await self._entry(request)
        name = _clean_name(entry["name"].replace("\\", "/").rsplit("/", 1)[-1])
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        tmp = os.path.join(self.root, f".raw-{secrets.token_hex(8)}")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._extract_entry, path, entry, self._password(meta["id"]), tmp)
            with open(tmp, "rb") as f:
                data = f.read()
        except PermissionError:
            return web.json_response({"error": "Arsip ini dikunci kata sandi"}, status=403)
        except ValueError:
            return self._too_big()
        except Exception as e:
            return web.json_response({"error": str(e) or "Gagal mengeluarkan file"}, status=422)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        inline = ext in IMAGE_EXT and request.query.get("download") != "1"
        ctype = mimetypes.types_map.get("." + ext, "application/octet-stream") if inline else "application/octet-stream"
        disposition = ("inline" if inline else "attachment") + f"; filename*=UTF-8''{quote(name)}"
        return web.Response(body=data, headers={"Content-Type": ctype, "Content-Disposition": disposition,
                                                "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                                                "Content-Security-Policy": "sandbox"})

    async def handle_original(self, request):
        meta = self._get_or_404(request)
        path = os.path.join(self._dir(meta["id"]), f"source.{meta['ext']}")
        if not os.path.exists(path):
            raise web.HTTPNotFound()
        ctype = next((m for m, e in MIME_EXT.items() if e == meta["ext"]), "application/octet-stream")
        disposition = f"attachment; filename*=UTF-8''{quote(meta['name'])}"
        return web.FileResponse(path, headers={"Content-Type": ctype, "Content-Disposition": disposition,
                                               "Cache-Control": "private, no-store"})

    async def handle_viewer(self, request):
        if not ID_RE.match(request.match_info["id"]):
            raise web.HTTPNotFound()
        return web.FileResponse(self.viewer_page, headers={"Cache-Control": "no-store"})

    async def handle_login_ok(self, request):
        # APK menunggu halaman ini setelah login untuk melanjutkan unggahan yang tertunda
        return web.Response(text="<!doctype html><meta charset=utf-8><title>Liliya</title>Login berhasil",
                            content_type="text/html", headers={"Cache-Control": "no-store"})

    # ---------- Konversi ----------
    def _schedule(self, doc_id: str):
        if doc_id in self._tasks:
            return
        task = asyncio.get_running_loop().create_task(self._convert(doc_id))
        self._tasks[doc_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(doc_id, None))

    async def _convert(self, doc_id: str):
        # Satu konversi sekaligus: LibreOffice berat untuk STB dan asisten suara/CCTV harus tetap lancar
        async with self._lock:
            meta = self._load(doc_id)
            if not meta or meta.get("status") != "converting":
                return
            started = time.time()
            ok, err = await asyncio.get_running_loop().run_in_executor(None, self._run_soffice, meta)
            meta = self._load(doc_id)
            if not meta:            # dihapus saat sedang dikonversi
                return
            meta["convert_seconds"] = round(time.time() - started, 1)
            if ok:
                meta.update(status="ready", error=None)
                print(f"[Docs] ✅ {meta['name']} siap ({meta['convert_seconds']} dtk)")
            else:
                meta.update(status="error", error=err)
                print(f"[Docs] ❌ {meta['name']} gagal: {err}")
            self._save(meta)

    def _run_soffice(self, meta: dict):
        folder = self._dir(meta["id"])
        src = os.path.join(folder, f"source.{meta['ext']}")
        out = os.path.join(folder, "out")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)

        target = "pdf"
        if meta["kind"] == "sheet":
            # Satu sheet = satu halaman utuh, supaya tabel lebar tidak terpotong ke banyak halaman
            target = 'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'
        cmd = ["nice", "-n", "10", self.soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault",
               "--nofirststartwizard", f"-env:UserInstallation=file://{self.profile}",
               "--convert-to", target, "--outdir", out]
        if meta["ext"] == "csv":
            cmd.append("--infilter=CSV:44,34,76,1")        # pemisah koma, kutip ganda, UTF-8
        elif meta["ext"] == "tsv":
            cmd.append("--infilter=CSV:9,34,76,1")
        cmd.append(src)

        for attempt in range(2):
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True, env={**os.environ, "HOME": self.root})
            try:
                log, _ = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                return False, f"Konversi terlalu lama (lebih dari {self.timeout // 60} menit). Coba unduh file aslinya."
            pdfs = [f for f in os.listdir(out) if f.lower().endswith(".pdf")]
            if pdfs:
                os.replace(os.path.join(out, pdfs[0]), os.path.join(folder, "view.pdf"))
                shutil.rmtree(out, ignore_errors=True)
                return True, None
            text = (log or b"").decode("utf-8", "replace").strip()
            # Profil LibreOffice rusak (mis. listrik padam saat konversi): buat ulang lalu coba sekali lagi
            if attempt == 0 and ("user installation" in text.lower() or proc.returncode in (77, 81)):
                shutil.rmtree(self.profile, ignore_errors=True)
                continue
            break
        shutil.rmtree(out, ignore_errors=True)
        print(f"[Docs] soffice keluar {proc.returncode}: {text[-400:]}")
        return False, "Dokumen tidak bisa dibuka. File mungkin rusak atau dilindungi kata sandi."

    # ---------- Pembersihan ----------
    async def _cleanup_loop(self):
        while True:
            try:
                self._cleanup()
            except Exception as e:
                print(f"[Docs Warning] Pembersihan gagal: {e}")
            await asyncio.sleep(6 * 3600)

    def _cleanup(self):
        cutoff = time.time() - self.retention_days * 86400
        for meta in self._all_meta():
            if meta.get("opened", 0) < cutoff and meta["id"] not in self._tasks:
                self._delete_dir(meta["id"])
                print(f"[Docs] 🧹 Dihapus (tidak dibuka {self.retention_days} hari): {meta['name']}")
        for name in os.listdir(self.root):       # sisa unggahan yang terputus
            path = os.path.join(self.root, name)
            if name.startswith(".upload-") and os.path.getmtime(path) < time.time() - 3600:
                os.unlink(path)

    def _enforce_quota(self, keep: str):
        """Batas total penyimpanan: hapus dokumen yang paling lama tidak dibuka."""
        docs = []
        total = 0
        for meta in self._all_meta():
            folder = self._dir(meta["id"])
            used = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder)
                       if os.path.isfile(os.path.join(folder, f)))
            total += used
            docs.append((meta.get("opened", 0), meta["id"], used))
        for _opened, doc_id, used in sorted(docs):
            if total <= self.max_total_bytes:
                break
            if doc_id == keep or doc_id in self._tasks:
                continue
            self._delete_dir(doc_id)
            total -= used
