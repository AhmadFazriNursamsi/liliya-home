import asyncio
import hashlib
import hmac
import ipaddress
import os
import time
from urllib.parse import quote

from aiohttp import web

COOKIE_NAME = "budi_session"
# /push/snap/: gambar notifikasi FCM, diakses lewat URL acak sekali pakai (lihat core/push.py)
PUBLIC_PATHS = ("/login", "/logout", "/static/", "/favicon.ico", "/push/snap/")
def _redirect(location: str) -> web.Response:
    return web.Response(status=302, headers={"Location": location, "Cache-Control": "no-store"})


DEFAULT_TRUSTED_NETWORKS = ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "100.64.0.0/10"]


class WebAuth:
    """Login berbasis cookie untuk Web UI / APK BUDI.

    - Akses dari jaringan lokal (WiFi rumah / Tailscale) boleh tanpa login (bisa dimatikan).
    - Akses lewat Cloudflare Tunnel selalu datang dari 127.0.0.1, sehingga wajib login.
    - Perangkat IoT / ESP32 bisa memakai header "X-Api-Key" atau "Authorization: Bearer <token>".
    """

    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("web_auth", {}) or {}
        self.enabled = cfg.get("enabled", False)
        self.password = str(cfg.get("password", "") or "")
        self.session_days = int(cfg.get("session_days", 60))
        self.api_tokens = [str(t) for t in (cfg.get("api_tokens") or []) if t]
        nets = cfg.get("trusted_networks", DEFAULT_TRUSTED_NETWORKS) or []
        self.trusted_networks = [ipaddress.ip_network(n, strict=False) for n in nets]
        self.secret = hashlib.sha256(("budi-session:" + self.password).encode()).digest()
        self.login_page = os.path.join(base_dir, "web", "login.html")
        self._failures = {}

        if self.enabled and not self.password:
            print("[WebAuth Warning] web_auth.enabled = true tapi password kosong. Login dinonaktifkan!")
            self.enabled = False
        if self.enabled:
            print(f"[WebAuth] 🔐 Login aktif. Jaringan tanpa login: {', '.join(str(n) for n in self.trusted_networks) or '-'}")

    # ---------- Session token ----------
    def _make_token(self) -> str:
        expiry = str(int(time.time()) + self.session_days * 86400)
        sig = hmac.new(self.secret, expiry.encode(), hashlib.sha256).hexdigest()
        return f"{expiry}.{sig}"

    def _valid_token(self, token: str) -> bool:
        try:
            expiry, sig = token.split(".", 1)
            good = hmac.new(self.secret, expiry.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(sig, good) and int(expiry) > time.time()
        except Exception:
            return False

    # ---------- Checks ----------
    def _is_trusted_peer(self, request) -> bool:
        try:
            ip = ipaddress.ip_address(request.remote or "")
        except ValueError:
            return False
        if ip.is_loopback:
            # Cloudflare Tunnel (cloudflared) terhubung dari localhost -> jangan dipercaya
            return False
        return any(ip in net for net in self.trusted_networks)

    def _has_api_token(self, request) -> bool:
        if not self.api_tokens:
            return False
        tok = request.headers.get("X-Api-Key", "")
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
        return any(hmac.compare_digest(tok, t) for t in self.api_tokens)

    def is_authenticated(self, request) -> bool:
        if not self.enabled:
            return True
        if self._is_trusted_peer(request) or self._has_api_token(request):
            return True
        return self._valid_token(request.cookies.get(COOKIE_NAME, ""))

    # ---------- Middleware ----------
    @web.middleware
    async def middleware(self, request, handler):
        path = request.path
        if not self.enabled or path.startswith(PUBLIC_PATHS) or self.is_authenticated(request):
            return await handler(request)
        if path.startswith("/api/") or path == "/ws" or path.startswith("/cctv/"):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return _redirect("/login?next=" + quote(request.path_qs, safe=""))

    # ---------- Login handlers ----------
    def _client_key(self, request) -> str:
        return request.headers.get("Cf-Connecting-Ip") or request.remote or "?"

    def _locked(self, key: str) -> bool:
        now = time.time()
        fails = [t for t in self._failures.get(key, []) if now - t < 600]
        self._failures[key] = fails
        total = sum(len([t for t in v if now - t < 600]) for v in self._failures.values())
        return len(fails) >= 5 or total >= 30

    async def handle_login_page(self, request):
        if self.is_authenticated(request):
            return _redirect(self._safe_next(request.query.get("next")))
        return web.FileResponse(self.login_page, headers={"Cache-Control": "no-store"})

    async def handle_login_submit(self, request):
        key = self._client_key(request)
        data = await request.post()
        nxt = self._safe_next(data.get("next"))
        if self._locked(key):
            return _redirect("/login?error=locked&next=" + quote(nxt, safe=""))

        if hmac.compare_digest(str(data.get("password", "")).encode(), self.password.encode()):
            self._failures.pop(key, None)
            resp = _redirect(nxt)
            secure = request.secure or request.headers.get("X-Forwarded-Proto") == "https" or "Cf-Connecting-Ip" in request.headers
            resp.set_cookie(COOKIE_NAME, self._make_token(), max_age=self.session_days * 86400,
                            httponly=True, samesite="Lax", secure=secure, path="/")
            print(f"[WebAuth] ✅ Login berhasil dari {key}")
            return resp

        self._failures.setdefault(key, []).append(time.time())
        print(f"[WebAuth] ⚠️ Password salah dari {key}")
        await asyncio.sleep(1.0)
        return _redirect("/login?error=1&next=" + quote(nxt, safe=""))

    async def handle_logout(self, request):
        resp = _redirect("/login")
        resp.del_cookie(COOKIE_NAME, path="/")
        return resp

    @staticmethod
    def _safe_next(nxt) -> str:
        nxt = str(nxt or "/")
        if not nxt.startswith("/") or nxt.startswith("//") or nxt.startswith("/login"):
            return "/"
        return nxt
