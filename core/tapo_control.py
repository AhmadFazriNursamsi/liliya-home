"""
Kontrol kamera Tapo lewat API lokal kamera (pytapo), tanpa cloud TP-Link:
mode privasi, deteksi gerakan/orang, mode malam, balik gambar, dan bicara lewat speaker kamera.

Bicara ("tahan untuk bicara" seperti aplikasi Tapo): rekaman dari HP diubah ffmpeg ke 8 kHz lalu
diputar go2rtc ke kamera lewat stream tapo:// yang punya jalur audio balik (backchannel).

Kamera hanya menerima login kontrol sebagai "admin" dengan password akun Tapo
(cctv.cameras[].tapo_cloud_password di config.yaml). Setelah beberapa login gagal kamera
mengunci login ±30 menit, jadi setelah gagal kita berhenti mencoba sampai waktunya lewat.
"""
import asyncio
import os
import re
import secrets
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import aiohttp
from aiohttp import web

try:
    from pytapo import Tapo
except ImportError:  # pragma: no cover - pytapo opsional
    Tapo = None

SENSITIVITY = ("low", "normal", "high")
NIGHT_MODES = ("auto", "on", "off")
TALK_MAX_SECONDS = 30
TALK_MAX_BYTES = 1_000_000
# Jeda awal agar suku kata pertama tidak terpotong, buang dengung rendah, ratakan volume suara
TALK_FILTER = "adelay=300,highpass=f=200,lowpass=f=3400,dynaudnorm=f=150:g=15,alimiter=limit=0.95"

# Bicara langsung (realtime): HP mengirim PCM 16-bit mono 16 kHz lewat WebSocket, go2rtc mengambilnya
# dari hub sebagai aliran WAV tanpa ujung dan meneruskannya ke speaker kamera.
LIVE_RATE = 16000
LIVE_FRAME = 640                 # 20 ms
LIVE_QUEUE_FRAMES = 25           # maks ±0,5 dtk antrean; lebih dari itu potongan lama dibuang agar jeda tidak menumpuk
LIVE_MAX_SECONDS = 15 * 60
LIVE_WAV_HEADER = (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt "
                   + struct.pack("<IHHIIHH", 16, 1, 1, LIVE_RATE, LIVE_RATE * 2, 2, 16)
                   + b"data" + struct.pack("<I", 0xFFFFFFFF))


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
            "flip": bool(self.call("getImageFlipVertical")),
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
        if "flip" in body:
            self.call("setImageFlipVertical", bool(body["flip"]))
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
        self.go2rtc_url = (cfg.get("go2rtc_url") or "http://127.0.0.1:1984").rstrip("/")
        self._talk_locks = {}
        self._live_feeds = {}     # token -> asyncio.Queue berisi PCM dari HP
        self._live_owner = {}     # cam_id -> token sesi bicara langsung yang aktif
        self._talk_dir = os.path.join(tempfile.gettempdir(), "budi-talk")
        if self.cameras:
            print(f"[Tapo] 🎛️ Kontrol kamera aktif: {', '.join(self.cameras)}")
        elif Tapo is None and cfg.get("cameras"):
            print("[Tapo] pytapo belum terpasang, kontrol kamera nonaktif")

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/cctv/{cam}/controls", self.handle_get)
        app.router.add_put("/api/cctv/{cam}/controls", self.handle_put)
        app.router.add_post("/api/cctv/{cam}/talk", self.handle_talk)
        app.router.add_get("/api/cctv/{cam}/talk/live", self.handle_talk_live)
        app.router.add_get("/api/cctv-feed/{token}", self.handle_live_feed)

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
        return web.json_response({**status, "talk": True})

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
        changes = ", ".join(f"{k}={body[k]}" for k in ("privacy", "motion", "sensitivity", "person", "night", "flip") if k in body)
        print(f"[Tapo] {cam_id}: {changes}")
        return web.json_response({**status, "talk": True})

    # ---------- Bicara lewat speaker kamera ----------
    async def _go2rtc(self, method: str, **params) -> tuple:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.request(method, f"{self.go2rtc_url}/api/streams", params=params) as r:
                return r.status, await r.text()

    async def _talk_stream(self, cam_id: str, cam: TapoCamera) -> str:
        """Stream tapo:// (punya backchannel audio) dibuat di memori go2rtc saat pertama dipakai."""
        name = f"{cam_id}_talk"
        if (await self._go2rtc("GET", src=name))[0] == 200:
            return name
        # go2rtc menjawab 400 karena tidak boleh menulis file config-nya, tapi stream tetap dibuat
        await self._go2rtc("PUT", name=name, src=f"tapo://{quote(cam.password, safe='')}@{cam.host}")
        if (await self._go2rtc("GET", src=name))[0] != 200:
            raise TapoError("go2rtc tidak bisa membuka jalur suara ke kamera.")
        return name

    async def handle_talk(self, request):
        cam_id, cam = self._camera(request)
        lock = self._talk_locks.setdefault(cam_id, asyncio.Lock())
        if lock.locked():
            return self._error(TapoError("Suara sebelumnya masih diputar di kamera, tunggu sebentar."))
        data = await request.content.read(TALK_MAX_BYTES + 1)
        if len(data) > TALK_MAX_BYTES:
            return self._error(TapoError(f"Rekaman terlalu panjang (maksimal {TALK_MAX_SECONDS} detik)."))
        if len(data) < 500:
            return self._error(TapoError("Rekaman kosong, coba tahan tombol lebih lama."))

        async with lock:
            os.makedirs(self._talk_dir, exist_ok=True)
            base = os.path.join(self._talk_dir, f"{cam_id}_{int(time.time() * 1000)}")
            src, wav = base + ".in", base + ".wav"
            try:
                with open(src, "wb") as f:
                    f.write(data)
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-v", "error", "-i", src, "-t", str(TALK_MAX_SECONDS), "-af", TALK_FILTER,
                    "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", wav,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(proc.communicate(), 30)
                if proc.returncode != 0 or not os.path.exists(wav):
                    print(f"[Tapo] {cam_id}: rekaman bicara gagal diubah: {err.decode(errors='replace')[-200:]}")
                    raise TapoError("Rekaman suara tidak bisa dibaca.")
                seconds = max(0.0, (os.path.getsize(wav) - 44) / 16000)   # PCM 16-bit mono 8 kHz

                name = await self._talk_stream(cam_id, cam)
                status, body = await self._go2rtc("POST", dst=name, src=f"ffmpeg:{wav}#input=file#audio=pcma")
                if status != 200 or '"format_name":"tapo"' not in body:
                    raise TapoError("Kamera tidak menerima suara. Pastikan mode privasi mati.")
                print(f"[Tapo] {cam_id}: 🎙️ bicara {seconds:.1f} dtk")
                # go2rtc membaca file secara real-time: tunggu sampai selesai diputar sebelum file dihapus
                await asyncio.sleep(seconds + 0.5)
            except TapoError as e:
                return self._error(e)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return self._error(TapoError("go2rtc tidak merespon."))
            finally:
                for path in (src, wav):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        return web.json_response({"status": "ok", "seconds": round(seconds, 1)})

    # ---------- Bicara langsung (realtime) ----------
    async def handle_talk_live(self, request):
        """WebSocket: frame biner = PCM s16le mono 16 kHz. Server mengirim {type: connecting|ready|error}."""
        cam_id, cam = self._camera(request)
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        if cam_id in self._live_owner or (self._talk_locks.get(cam_id) and self._talk_locks[cam_id].locked()):
            await ws.send_json({"type": "error", "message": "Speaker kamera sedang dipakai perangkat lain."})
            await ws.close()
            return ws

        token = secrets.token_urlsafe(32)
        queue = asyncio.Queue(maxsize=LIVE_QUEUE_FRAMES * 2)
        self._live_feeds[token] = queue
        self._live_owner[cam_id] = token
        port = (request.transport.get_extra_info("sockname") or ("", 8080))[1]
        started = time.time()
        frames = 0

        async def connect():
            try:
                await ws.send_json({"type": "connecting"})
                name = await self._talk_stream(cam_id, cam)
                status, body = await self._go2rtc(
                    "POST", dst=name, src=f"ffmpeg:http://127.0.0.1:{port}/api/cctv-feed/{token}#audio=pcma")
                if status != 200 or '"format_name":"tapo"' not in body:
                    raise TapoError("Kamera tidak menerima suara. Pastikan mode privasi mati.")
                print(f"[Tapo] {cam_id}: 🎙️ bicara langsung tersambung ({time.time() - started:.1f} dtk)")
                await ws.send_json({"type": "ready"})
            except TapoError as e:
                await ws.send_json({"type": "error", "message": str(e)})
                await ws.close()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await ws.send_json({"type": "error", "message": "go2rtc tidak merespon."})
                await ws.close()
            except ConnectionResetError:
                pass

        task = asyncio.create_task(connect())
        try:
            async for msg in ws:
                if time.time() - started > LIVE_MAX_SECONDS:
                    break
                if msg.type != aiohttp.WSMsgType.BINARY or not msg.data or len(msg.data) % 2:
                    continue
                if queue.full():
                    try:
                        queue.get_nowait()   # buang yang paling lama: jeda tetap pendek walau sinyal tersendat
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(msg.data)
                frames += 1
        finally:
            task.cancel()
            self._live_feeds.pop(token, None)
            if self._live_owner.get(cam_id) == token:
                self._live_owner.pop(cam_id, None)
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            print(f"[Tapo] {cam_id}: 🎙️ bicara langsung selesai ({time.time() - started:.0f} dtk, {frames} potongan)")
        return ws

    async def handle_live_feed(self, request):
        """Aliran WAV untuk ffmpeg milik go2rtc. Hanya dari STB sendiri, dengan token sesi yang masih aktif."""
        if request.remote not in ("127.0.0.1", "::1") or any(
                h in request.headers for h in ("Cf-Connecting-Ip", "X-Forwarded-For", "Origin")):
            raise web.HTTPNotFound()
        token = request.match_info.get("token", "")
        queue = self._live_feeds.get(token)
        if queue is None:
            raise web.HTTPNotFound()
        resp = web.StreamResponse(headers={"Content-Type": "audio/wav", "Cache-Control": "no-store"})
        await resp.prepare(request)
        await resp.write(LIVE_WAV_HEADER)
        silence = b"\0" * (LIVE_FRAME * 5)
        try:
            while token in self._live_feeds:
                try:
                    chunk = await asyncio.wait_for(queue.get(), 0.1)
                except asyncio.TimeoutError:
                    chunk = silence   # tidak sedang bicara: isi hening agar sambungan ke kamera tetap hidup
                if chunk is None:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp
