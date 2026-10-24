"""
Kontrol kamera Tapo lewat API lokal kamera (pytapo), tanpa cloud TP-Link:
mode privasi, deteksi gerakan/orang, mode malam, dan sirene.

Kamera hanya menerima login kontrol sebagai "admin" dengan password akun Tapo
(cctv.cameras[].tapo_cloud_password di config.yaml). Setelah beberapa login gagal kamera
mengunci login ±30 menit, jadi setelah gagal kita berhenti mencoba sampai waktunya lewat.
"""
import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

try:
    from pytapo import Tapo
except ImportError:  # pragma: no cover - pytapo opsional
    Tapo = None

SENSITIVITY = ("low", "normal", "high")
NIGHT_MODES = ("auto", "on", "off")
SIREN_MAX_SECONDS = 60


class TapoCamera:
    def __init__(self, host: str, password: str):
        self.host = host
        self.password = password
        self._client = None
        self.blocked_until = 0.0
        self.block_reason = ""

    def client(self):
        if time.time() < self.blocked_until:
            mins = max(1, round((self.blocked_until - time.time()) / 60))
            raise TapoError(f"{self.block_reason} Coba lagi ±{mins} menit.")
        if self._client is None:
            try:
                self._client = Tapo(self.host, "admin", self.password)
            except Exception as e:
                self._login_failed(str(e))
        return self._client

    def _login_failed(self, msg: str):
        m = re.search(r"Try again in (\d+) seconds", msg)
        if m:
            self.blocked_until = time.time() + int(m.group(1))
            self.block_reason = "Kamera mengunci login sementara karena terlalu banyak percobaan gagal."
        elif "Invalid authentication" in msg:
            # Jangan coba terus dengan password salah: kamera bisa mengunci login 30 menit
            self.blocked_until = time.time() + 600
            self.block_reason = "Password akun Tapo di config.yaml salah."
        else:
            raise TapoError(f"Kamera tidak bisa dihubungi: {msg}")
        raise TapoError(self.block_reason)

    def call(self, fn, *args):
        client = self.client()
        try:
            return getattr(client, fn)(*args)
        except Exception as e:
            # Sesi kamera kedaluwarsa / kamera restart: login ulang sekali
            self._client = None
            client = self.client()
            try:
                return getattr(client, fn)(*args)
            except Exception as e2:
                self._client = None
                raise TapoError(str(e2) or str(e)) from e2

    # ---------- Status & pengaturan ----------
    def status(self) -> dict:
        motion = self.call("getMotionDetection")
        result = {
            "privacy": self.call("getPrivacyMode").get("enabled") == "on",
            "motion": motion.get("enabled") == "on",
            "sensitivity": self._sensitivity_label(motion),
            "night": self.call("getDayNightMode"),
            "person": None,
        }
        try:
            result["person"] = self.call("getPersonDetection").get("enabled") == "on"
        except TapoError:
            pass  # model tanpa deteksi orang
        return result

    @staticmethod
    def _sensitivity_label(motion: dict) -> str:
        label = motion.get("sensitivity")
        if label in SENSITIVITY:
            return label
        try:
            n = int(motion.get("digital_sensitivity", 50))
        except (TypeError, ValueError):
            return "normal"
        return "low" if n < 40 else "high" if n > 60 else "normal"

    def update(self, body: dict):
        if "privacy" in body:
            self.call("setPrivacyMode", bool(body["privacy"]))
        if "motion" in body or "sensitivity" in body:
            sens = body.get("sensitivity")
            if sens is not None and sens not in SENSITIVITY:
                raise TapoError("Sensitivitas harus low/normal/high")
            enabled = bool(body["motion"]) if "motion" in body else None
            self.call("setMotionDetection", enabled, sens or False)
        if "person" in body:
            self.call("setPersonDetection", bool(body["person"]))
        if "night" in body:
            if body["night"] not in NIGHT_MODES:
                raise TapoError("Mode malam harus auto/on/off")
            self.call("setDayNightMode", body["night"])


class TapoError(Exception):
    pass


class TapoControlManager:
    def __init__(self, config: dict):
        cfg = config.get("cctv", {}) or {}
        self.cameras = {}
        for cam in cfg.get("cameras", []) or []:
            cam_id = str(cam.get("id", "")).strip()
            host = (cam.get("onvif") or {}).get("host")
            password = cam.get("tapo_cloud_password")
            if cam_id and host and password and Tapo is not None:
                self.cameras[cam_id] = TapoCamera(host, str(password))
        # pytapo memakai event loop sendiri per objek: semua panggilan lewat satu thread
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tapo")
        self._siren_stop = {}
        if self.cameras:
            print(f"[Tapo] 🎛️ Kontrol kamera aktif: {', '.join(self.cameras)}")
        elif Tapo is None and cfg.get("cameras"):
            print("[Tapo] pytapo belum terpasang, kontrol kamera nonaktif")

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/cctv/{cam}/controls", self.handle_get)
        app.router.add_put("/api/cctv/{cam}/controls", self.handle_put)
        app.router.add_post("/api/cctv/{cam}/siren", self.handle_siren)

    def _camera(self, request) -> tuple:
        cam_id = request.match_info.get("cam", "")
        cam = self.cameras.get(cam_id)
        if not cam:
            raise web.HTTPNotFound(text="Kontrol kamera tidak tersedia")
        return cam_id, cam

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    @staticmethod
    def _error(e: Exception):
        # 409, bukan 5xx: Cloudflare mengganti isi respons 5xx sehingga pesan tidak sampai ke aplikasi
        return web.json_response({"error": str(e)}, status=409)

    async def handle_get(self, request):
        cam_id, cam = self._camera(request)
        try:
            status = await self._run(cam.status)
        except TapoError as e:
            return self._error(e)
        return web.json_response({**status, "siren": cam_id in self._siren_stop})

    async def handle_put(self, request):
        cam_id, cam = self._camera(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            await self._run(cam.update, body)
            status = await self._run(cam.status)
        except TapoError as e:
            return self._error(e)
        changes = ", ".join(f"{k}={body[k]}" for k in ("privacy", "motion", "sensitivity", "person", "night") if k in body)
        print(f"[Tapo] {cam_id}: {changes}")
        return web.json_response({**status, "siren": cam_id in self._siren_stop})

    async def handle_siren(self, request):
        cam_id, cam = self._camera(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        on = bool(body.get("on"))
        timer = self._siren_stop.pop(cam_id, None)
        if timer:
            timer.cancel()
        try:
            await self._run(cam.call, "startManualAlarm" if on else "stopManualAlarm")
        except TapoError as e:
            return self._error(e)
        if on:
            # Pengaman: sirene berhenti sendiri walau aplikasi ditutup
            self._siren_stop[cam_id] = asyncio.get_running_loop().call_later(
                SIREN_MAX_SECONDS, lambda: asyncio.ensure_future(self._auto_stop(cam_id, cam)))
        print(f"[Tapo] {cam_id}: sirene {'NYALA' if on else 'mati'}")
        return web.json_response({"siren": on, "max_seconds": SIREN_MAX_SECONDS})

    async def _auto_stop(self, cam_id: str, cam: TapoCamera):
        self._siren_stop.pop(cam_id, None)
        try:
            await self._run(cam.call, "stopManualAlarm")
        except TapoError as e:
            print(f"[Tapo] {cam_id}: gagal mematikan sirene otomatis: {e}")
