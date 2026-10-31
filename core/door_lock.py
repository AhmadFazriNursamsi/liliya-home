"""Kunci pintu pintar (ESP32-C3 + RFID + servo + sensor IR pintu).

Logika kunci tetap berjalan di ESP32; server hanya menerima laporan kejadian,
menyimpan riwayat, mengirim notifikasi, dan meneruskan perintah buka/kunci dari
aplikasi atau suara. ESP32 terhubung seperti voice-worker: header X-Device-Token
dan long-poll perintah (sekaligus menjadi tanda online).
"""
import asyncio
import hmac
import json
import os
import secrets
import time
import uuid

from aiohttp import web

DEVICE_PREFIX = "/api/door-device/"
MAX_POLL_S = 25
CMD_TTL_S = 20          # perintah yang tidak diambil ESP32 dalam 20 dtk dibuang (jangan buka pintu terlambat)
CMD_RESULT_WAIT_S = 8
HISTORY_MAX = 200
REMINDER_PUSH_COOLDOWN = 300

EVENT_TEXT = {
    "boot": "Kunci pintu menyala",
    "unlock": "Kunci dibuka",
    "lock": "Pintu dikunci",
    "lock_rejected": "Gagal mengunci: pintu masih terbuka",
    "door_open": "Pintu terbuka",
    "door_closed": "Pintu tertutup",
    "unknown_card": "Kartu tidak dikenal ditempel",
    "denied_card": "Kartu tanpa akses ditempel",
    "reminder": "Pintu terbuka terlalu lama",
    "offline": "Kunci pintu offline",
    "online": "Kunci pintu online kembali",
}


class DoorLockManager:
    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("door_lock", {}) or {}
        self.name = str(cfg.get("name", "Pintu Depan"))
        self.offline_after = int(cfg.get("offline_after", 90))
        self.state_path = os.path.join(base_dir, "data", "door_lock.json")
        self.secret_path = os.path.join(base_dir, "data", "door_lock_token.json")
        self.token = str(cfg.get("token") or self._load_or_create_token())
        self.push = None          # PushManager, diisi oleh WebServer
        self.broadcast = None     # WebServer.broadcast(event_type, payload)
        self.loop = None

        saved = self._read_json(self.state_path, {})
        self.history = saved.get("history", [])[-HISTORY_MAX:]
        self.state = {"locked": True, "door_closed": None, **saved.get("state", {})}
        self.last_seen = 0.0
        self.online = False
        self._pending = None      # {"id", "cmd", "by", "ts"}
        self._cmd_event = None
        self._results = {}        # cmd_id -> Future
        self._last_reminder_push = 0.0
        self._watch_task = None
        print(f"[DoorLock] 🔐 Kunci pintu '{self.name}' siap (token di {os.path.relpath(self.secret_path, base_dir)})")

    # ---------- Penyimpanan ----------
    @staticmethod
    def _read_json(path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"state": self.state, "history": self.history[-HISTORY_MAX:]}, f)
        os.replace(tmp, self.state_path)

    def _load_or_create_token(self) -> str:
        data = self._read_json(self.secret_path, {})
        if data.get("token"):
            return data["token"]
        token = secrets.token_urlsafe(32)
        os.makedirs(os.path.dirname(self.secret_path), exist_ok=True)
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": token}, f)
        return token

    # ---------- Status ----------
    def _is_online(self) -> bool:
        return self.last_seen > 0 and time.time() - self.last_seen < self.offline_after

    def status(self) -> dict:
        return {
            "name": self.name,
            "online": self._is_online(),
            "last_seen_s": round(time.time() - self.last_seen) if self.last_seen else None,
            "locked": self.state.get("locked"),
            "door_closed": self.state.get("door_closed"),
            "updated": self.state.get("updated"),
            "history": self.history[-30:][::-1],
        }

    def describe(self) -> str:
        """Kalimat status untuk asisten suara."""
        s = self.status()
        if not s["online"]:
            return f"Kunci {self.name} sedang offline, status terakhir {'terkunci' if s['locked'] else 'tidak terkunci'}."
        door = {True: "tertutup", False: "terbuka", None: "tidak diketahui"}[s["door_closed"]]
        return f"{self.name} {'terkunci' if s['locked'] else 'tidak terkunci'}, pintunya {door}."

    def _record(self, kind: str, detail: str = "", by: str = ""):
        entry = {"ts": int(time.time()), "type": kind, "text": EVENT_TEXT.get(kind, kind)}
        if detail:
            entry["detail"] = detail
        if by:
            entry["by"] = by
        self.history.append(entry)
        self.history = self.history[-HISTORY_MAX:]
        self._save()
        if self.broadcast:
            self.broadcast("door_lock", {"status": self.status(), "event": entry})
        return entry

    async def _notify(self, title: str, body: str):
        if self.push:
            try:
                await self.push.send(title, body, data={"type": "door", "open": "home"})
            except Exception as e:
                print(f"[DoorLock] Notifikasi gagal: {e}")

    def _touch(self):
        was_online = self._is_online()
        self.last_seen = time.time()
        if not was_online and self.online is False and self.history:
            self._record("online")
        self.online = True

    # ---------- Perintah (dari aplikasi / suara) ----------
    async def queue_command(self, cmd: str, by: str) -> dict:
        if cmd not in ("lock", "unlock"):
            return {"ok": False, "error": "Perintah tidak dikenal"}
        if not self._is_online():
            return {"ok": False, "error": f"Kunci {self.name} sedang offline."}
        cmd_id = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._results[cmd_id] = fut
        self._pending = {"id": cmd_id, "cmd": cmd, "by": by, "ts": time.time()}
        if self._cmd_event:
            self._cmd_event.set()
        try:
            return await asyncio.wait_for(fut, CMD_RESULT_WAIT_S)
        except asyncio.TimeoutError:
            if self._pending and self._pending["id"] == cmd_id:
                self._pending = None
            return {"ok": False, "error": "Kunci pintu tidak merespons."}
        finally:
            self._results.pop(cmd_id, None)

    def queue_command_threadsafe(self, cmd: str, by: str, timeout: float = CMD_RESULT_WAIT_S + 2) -> dict:
        """Untuk skill suara (berjalan di thread lain)."""
        if not self.loop:
            return {"ok": False, "error": "Server kunci pintu belum siap."}
        fut = asyncio.run_coroutine_threadsafe(self.queue_command(cmd, by), self.loop)
        try:
            return fut.result(timeout)
        except Exception:
            return {"ok": False, "error": "Kunci pintu tidak merespons."}

    # ---------- HTTP: ESP32 ----------
    def _authorized(self, request) -> bool:
        tok = request.headers.get("X-Device-Token", "")
        return bool(tok) and hmac.compare_digest(tok.encode(), self.token.encode())

    async def _deny(self):
        await asyncio.sleep(1.0)
        return web.json_response({"error": "Unauthorized"}, status=401)

    def _ensure_loop(self):
        if not self.loop:
            self.loop = asyncio.get_running_loop()
            self._cmd_event = asyncio.Event()
            self._watch_task = self.loop.create_task(self._watch_offline())

    async def handle_device_command(self, request):
        if not self._authorized(request):
            return await self._deny()
        self._ensure_loop()
        self._touch()
        try:
            wait_s = min(float(request.query.get("wait", MAX_POLL_S)), MAX_POLL_S)
        except ValueError:
            wait_s = MAX_POLL_S
        deadline = time.time() + wait_s
        while True:
            p = self._pending
            if p and time.time() - p["ts"] > CMD_TTL_S:
                self._pending = p = None
            if p:
                self._pending = None
                self._touch()
                return web.json_response({"cmd": p["cmd"], "id": p["id"], "by": p["by"]})
            remaining = deadline - time.time()
            if remaining <= 0:
                self._touch()
                return web.json_response({"cmd": None})
            self._cmd_event.clear()
            try:
                await asyncio.wait_for(self._cmd_event.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def handle_device_event(self, request):
        if not self._authorized(request):
            return await self._deny()
        self._ensure_loop()
        self._touch()
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "JSON tidak valid"}, status=400)
        kind = str(body.get("type", ""))[:20]
        by = str(body.get("by", ""))[:40]
        uid = str(body.get("uid", ""))[:32]
        if "locked" in body:
            self.state["locked"] = bool(body["locked"])
        if "door_closed" in body:
            self.state["door_closed"] = bool(body["door_closed"])
        self.state["updated"] = int(time.time())

        if kind in EVENT_TEXT:
            detail = uid if kind in ("unknown_card",) else ""
            self._record(kind, detail=detail, by=by)
        else:
            self._save()

        fut = self._results.get(str(body.get("cmd_id", "")))
        if fut and not fut.done():
            fut.set_result({"ok": kind != "lock_rejected", "event": kind,
                            "error": EVENT_TEXT["lock_rejected"] if kind == "lock_rejected" else "",
                            "status": self.status()})

        if kind == "unknown_card":
            await self._notify("🚨 Kartu tidak dikenal", f"Ada yang menempelkan kartu asing di {self.name} ({uid}).")
        elif kind == "reminder" and time.time() - self._last_reminder_push > REMINDER_PUSH_COOLDOWN:
            self._last_reminder_push = time.time()
            await self._notify("🚪 Pintu belum ditutup", f"{self.name} terbuka lebih dari 1 menit.")
        return web.json_response({"ok": True})

    async def _watch_offline(self):
        while True:
            await asyncio.sleep(15)
            if self.online and not self._is_online():
                self.online = False
                self._record("offline")
                await self._notify("⚠️ Kunci pintu offline", f"{self.name} tidak terhubung ke Liliya. Kunci tetap bekerja dengan kartu.")

    # ---------- HTTP: aplikasi (wajib login) ----------
    async def handle_status(self, request):
        return web.json_response(self.status())

    async def handle_command(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        self._ensure_loop()
        res = await self.queue_command(str(body.get("action", "")), by="Aplikasi")
        return web.json_response({**res, "status": self.status()}, status=200 if res.get("ok") else 409)

    def register_routes(self, app: web.Application):
        app.router.add_get(DEVICE_PREFIX + "command", self.handle_device_command)
        app.router.add_post(DEVICE_PREFIX + "event", self.handle_device_event)
        app.router.add_get("/api/door/status", self.handle_status)
        app.router.add_post("/api/door/command", self.handle_command)
