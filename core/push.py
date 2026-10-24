import asyncio
import json
import os
import secrets
import threading
import time

import aiohttp
from aiohttp import web

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest
except ImportError:  # google-auth ikut terpasang bersama google-genai
    service_account = None

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SNAPSHOT_TTL = 24 * 3600   # gambar notifikasi bisa dibuka selama 24 jam
SNAPSHOT_MAX = 200


class PushManager:
    """Notifikasi FCM ke APK Liliya (mis. gerakan CCTV) via FCM HTTP v1 + service account."""

    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("push", {}) or {}
        self.service_account_file = cfg.get("service_account", "")
        if self.service_account_file and not os.path.isabs(self.service_account_file):
            self.service_account_file = os.path.join(base_dir, self.service_account_file)
        self.public_url = str(cfg.get("public_url", "https://smarthome.laravelia.my.id")).rstrip("/")
        self.motion_cooldown = int(cfg.get("motion_cooldown", 60))
        self.tokens_path = os.path.join(base_dir, "data", "push_tokens.json")
        self.settings_path = os.path.join(base_dir, "data", "push_settings.json")
        self.enabled = bool(cfg.get("enabled") and service_account and os.path.isfile(self.service_account_file))
        self._credentials = None
        self._project_id = ""
        self._cred_lock = threading.Lock()
        self._snapshots = {}            # id -> (expires_at, jpeg bytes)
        self._last_motion_push = {}     # (cam_id, kind) -> ts
        self._session = None
        if self.enabled:
            with open(self.service_account_file) as f:
                self._project_id = json.load(f).get("project_id", "")
            print(f"[Push] 🔔 Notifikasi FCM aktif (project {self._project_id}, {len(self._load_tokens())} perangkat)")
        elif cfg.get("enabled"):
            print("[Push Warning] push.enabled=true tapi file service account tidak ditemukan / google-auth tidak tersedia.")

    def register_routes(self, app: web.Application):
        app.router.add_post("/api/push/register", self.handle_register)
        app.router.add_get("/api/push/settings", self.handle_get_settings)
        app.router.add_put("/api/push/settings", self.handle_put_settings)
        app.router.add_post("/api/push/test", self.handle_test)
        app.router.add_get("/push/snap/{sid}.jpg", self.handle_snapshot)

    # ---------- Penyimpanan ----------
    def _read_json(self, path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _load_tokens(self) -> dict:
        return self._read_json(self.tokens_path, {})

    def settings(self) -> dict:
        return {"motion": True, **self._read_json(self.settings_path, {})}

    # ---------- FCM ----------
    def _access_token(self) -> str:
        with self._cred_lock:
            if self._credentials is None:
                self._credentials = service_account.Credentials.from_service_account_file(
                    self.service_account_file, scopes=[FCM_SCOPE])
            if not self._credentials.valid:
                self._credentials.refresh(GoogleRequest())
            return self._credentials.token

    async def _http(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def send(self, title: str, body: str, data: dict = None, image_url: str = "", channel: str = "motion") -> int:
        """Kirim ke semua perangkat terdaftar. Mengembalikan jumlah yang berhasil."""
        tokens = self._load_tokens()
        if not self.enabled or not tokens:
            return 0
        try:
            access = await asyncio.get_running_loop().run_in_executor(None, self._access_token)
        except Exception as e:
            print(f"[Push] Gagal mengambil token Google: {e}")
            return 0
        url = f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send"
        notification = {"title": title, "body": body, **({"image": image_url} if image_url else {})}
        session = await self._http()
        sent, dead = 0, []
        for token in list(tokens):
            message = {"message": {
                "token": token,
                "notification": notification,
                "data": {k: str(v) for k, v in (data or {}).items()},
                "android": {"priority": "HIGH", "ttl": "600s", "notification": {
                    "channel_id": channel, "tag": f"{channel}-{(data or {}).get('cam', '')}",
                    "notification_priority": "PRIORITY_MAX", "default_sound": True, "visibility": "PRIVATE"}},
            }}
            try:
                async with session.post(url, json=message, headers={"Authorization": f"Bearer {access}"}) as r:
                    if r.status == 200:
                        sent += 1
                    else:
                        err = await r.text()
                        if r.status in (400, 404) and ("UNREGISTERED" in err or "INVALID_ARGUMENT" in err and "token" in err.lower()):
                            dead.append(token)
                        else:
                            print(f"[Push] FCM {r.status}: {err[:200]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"[Push] Gagal mengirim FCM: {e}")
        if dead:
            for t in dead:
                tokens.pop(t, None)
            self._write_json(self.tokens_path, tokens)
            print(f"[Push] {len(dead)} token kedaluwarsa dihapus")
        return sent

    # ---------- Gerakan CCTV ----------
    def store_snapshot(self, jpeg: bytes) -> str:
        now = time.time()
        self._snapshots = {k: v for k, v in self._snapshots.items() if v[0] > now}
        while len(self._snapshots) >= SNAPSHOT_MAX:
            self._snapshots.pop(next(iter(self._snapshots)))
        sid = secrets.token_urlsafe(24)   # tidak bisa ditebak; hanya penerima notifikasi yang tahu URL-nya
        self._snapshots[sid] = (now + SNAPSHOT_TTL, jpeg)
        return f"{self.public_url}/push/snap/{sid}.jpg"

    async def notify_motion(self, cam_id: str, cam_name: str, kind: str, ts: float, snapshot_coro=None):
        if not self.enabled or not self.settings().get("motion", True):
            return
        key = (cam_id, kind)
        if ts - self._last_motion_push.get(key, 0) < self.motion_cooldown:
            return
        self._last_motion_push[key] = ts
        image_url = ""
        if snapshot_coro is not None:
            try:
                jpeg = await snapshot_coro
                if jpeg:
                    image_url = self.store_snapshot(jpeg)
            except Exception as e:
                print(f"[Push] Snapshot gagal: {e}")
        clock = time.strftime("%H:%M:%S", time.localtime(ts))
        title = "🚶 Orang terdeteksi" if kind == "people" else "Gerakan terdeteksi"
        sent = await self.send(title, f"{cam_name} · {clock}", data={
            "type": "motion", "cam": cam_id, "ts": int(ts), "kind": kind, "open": "cctv"}, image_url=image_url)
        print(f"[Push] 🔔 Notifikasi {kind} '{cam_id}' {clock} terkirim ke {sent} perangkat")

    # ---------- API ----------
    async def handle_register(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = str(body.get("token", "")).strip()
        if not (100 <= len(token) <= 4096):
            return web.json_response({"error": "Token tidak valid"}, status=400)
        tokens = self._load_tokens()
        is_new = token not in tokens
        tokens[token] = {"device": str(body.get("device", ""))[:60], "last_seen": int(time.time()),
                         "added": tokens.get(token, {}).get("added", int(time.time()))}
        self._write_json(self.tokens_path, tokens)
        if is_new:
            print(f"[Push] 📱 Perangkat baru terdaftar: {tokens[token]['device'] or 'Android'} ({len(tokens)} total)")
        return web.json_response({"status": "ok", "enabled": self.enabled})

    async def handle_get_settings(self, request):
        return web.json_response({"enabled": self.enabled, "devices": len(self._load_tokens()), **self.settings()})

    async def handle_put_settings(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        current = self.settings()
        if "motion" in body:
            current["motion"] = bool(body["motion"])
        self._write_json(self.settings_path, current)
        return web.json_response({"enabled": self.enabled, "devices": len(self._load_tokens()), **current})

    async def handle_test(self, request):
        if not self.enabled:
            return web.json_response({"error": "Notifikasi belum dikonfigurasi di server (push.service_account)."}, status=409)
        sent = await self.send("Tes notifikasi Liliya", "Notifikasi gerakan CCTV sudah aktif di HP ini.",
                               data={"type": "test", "open": "cctv"})
        return web.json_response({"status": "ok", "sent": sent})

    async def handle_snapshot(self, request):
        item = self._snapshots.get(request.match_info.get("sid", ""))
        if not item or item[0] < time.time():
            raise web.HTTPNotFound()
        return web.Response(body=item[1], content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
