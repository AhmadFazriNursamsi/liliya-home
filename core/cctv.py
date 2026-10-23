import asyncio
import base64
import datetime
import hashlib
import json
import os
import re
import time

import aiohttp
import requests
from aiohttp import web

SEGMENT_RE = re.compile(r"^(\d{8})_(\d{6})\.mp4$")
# File statis player go2rtc yang boleh diambil lewat BUDI (API konfigurasi go2rtc TIDAK diekspos)
PLAYER_FILES = {"video-stream.js", "video-rtc.js"}


class OnvifPTZ:
    """Kontrol pan-tilt kamera (Tapo C200 dll) via ONVIF RelativeMove."""

    def __init__(self, host, port=2020, username="", password=""):
        self.url = f"http://{host}:{port}/onvif/service"
        self.username = username
        self.password = str(password)
        self.profile_token = None

    def _security_header(self):
        nonce = os.urandom(16)
        created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (
            '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            f"<UsernameToken><Username>{self.username}</Username>"
            f'<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
            f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>'
            f'<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>'
            "</UsernameToken></Security></s:Header>"
        )

    def _call(self, body: str) -> str:
        envelope = f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">{self._security_header()}<s:Body>{body}</s:Body></s:Envelope>'
        r = requests.post(self.url, data=envelope, headers={"Content-Type": "application/soap+xml"}, timeout=5)
        r.raise_for_status()
        return r.text

    def _get_profile(self) -> str:
        if not self.profile_token:
            xml = self._call('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
            m = re.search(r'Profiles[^>]*token="([^"]+)"', xml)
            if not m:
                raise RuntimeError("Profil ONVIF tidak ditemukan")
            self.profile_token = m.group(1)
        return self.profile_token

    def move(self, x: float, y: float):
        token = self._get_profile()
        self._call(
            f'<RelativeMove xmlns="http://www.onvif.org/ver20/ptz/wsdl"><ProfileToken>{token}</ProfileToken>'
            f'<Translation><PanTilt xmlns="http://www.onvif.org/ver10/schema" x="{x:.3f}" y="{y:.3f}"/></Translation></RelativeMove>'
        )


class CCTVManager:
    def __init__(self, config: dict):
        cfg = config.get("cctv", {}) or {}
        self.enabled = cfg.get("enabled", False)
        self.go2rtc_url = cfg.get("go2rtc_url", "http://127.0.0.1:1984").rstrip("/")
        self.recordings_dir = cfg.get("recordings_dir", "/srv/cctv")
        self.segment_seconds = int(cfg.get("segment_seconds", 600))
        self.cameras = {}
        self.ptz = {}
        for cam in cfg.get("cameras", []) or []:
            cam_id = str(cam.get("id", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", cam_id):
                continue
            self.cameras[cam_id] = {
                "id": cam_id,
                "name": cam.get("name", cam_id),
                "live_stream": cam.get("live_stream", cam_id),
                "hd_stream": cam.get("hd_stream", cam_id),
            }
            onvif = cam.get("onvif") or {}
            if onvif.get("host"):
                self.ptz[cam_id] = OnvifPTZ(onvif["host"], onvif.get("port", 2020),
                                            onvif.get("username", ""), onvif.get("password", ""))
        self.allowed_streams = {s for c in self.cameras.values() for s in (c["live_stream"], c["hd_stream"])}
        self._player_cache = {}
        self._session = None
        if self.enabled:
            print(f"[CCTV] 📹 {len(self.cameras)} kamera aktif, rekaman di {self.recordings_dir}")

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/cctv/cameras", self.handle_cameras)
        app.router.add_get("/api/cctv/backup", self.handle_backup)
        app.router.add_get("/api/cctv/{cam}/recordings", self.handle_recordings)
        app.router.add_get("/api/cctv/{cam}/snapshot", self.handle_snapshot)
        app.router.add_post("/api/cctv/{cam}/ptz", self.handle_ptz)
        app.router.add_get("/cctv/rec/{cam}/{file}", self.handle_recording_file)
        app.router.add_get("/cctv/live/api/ws", self.handle_live_ws)
        app.router.add_get("/cctv/live/{file}", self.handle_player_file)

    def _camera(self, request):
        cam = self.cameras.get(request.match_info.get("cam", ""))
        if not cam:
            raise web.HTTPNotFound(text="Kamera tidak ditemukan")
        return cam

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
        return self._session

    # ---------- Status backup Google Drive (ditulis oleh /usr/local/bin/cctv-upload) ----------
    async def handle_backup(self, request):
        path = os.path.join(self.recordings_dir, ".upload_status.json")
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return web.json_response({"enabled": False})
        drive = data.get("drive") or {}
        return web.json_response({
            "enabled": True,
            "last_run": data.get("last_run", 0),
            "last_success": data.get("last_success", 0),
            "pending_files": data.get("pending_files", 0),
            "pending_bytes": data.get("pending_bytes", 0),
            "error": data.get("error", ""),
            "drive_total": drive.get("total"),
            "drive_free": drive.get("free"),
        })

    # ---------- Info & rekaman ----------
    async def handle_cameras(self, request):
        cams = [{**c, "ptz": c["id"] in self.ptz} for c in self.cameras.values()]
        return web.json_response({"enabled": self.enabled, "cameras": cams})

    def _list_segments(self, cam_id: str):
        folder = os.path.join(self.recordings_dir, cam_id)
        result = []
        try:
            names = os.listdir(folder)
        except FileNotFoundError:
            return result
        now = time.time()
        for name in names:
            m = SEGMENT_RE.match(name)
            if not m:
                continue
            try:
                st = os.stat(os.path.join(folder, name))
                start = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            except (OSError, ValueError):
                continue
            result.append({
                "file": name,
                "date": start.strftime("%Y-%m-%d"),
                "start": start.strftime("%H:%M:%S"),
                "start_ts": int(start.timestamp()),
                "end_ts": int(st.st_mtime),
                "size": st.st_size,
                "recording": now - st.st_mtime < 20,
            })
        result.sort(key=lambda s: s["start_ts"])
        return result

    async def handle_recordings(self, request):
        cam = self._camera(request)
        segments = await asyncio.get_running_loop().run_in_executor(None, self._list_segments, cam["id"])
        dates = sorted({s["date"] for s in segments}, reverse=True)
        date = request.query.get("date") or (dates[0] if dates else "")
        return web.json_response({
            "camera": cam["id"],
            "dates": dates,
            "date": date,
            "segments": [s for s in segments if s["date"] == date],
        })

    async def handle_recording_file(self, request):
        cam = self._camera(request)
        name = request.match_info.get("file", "")
        if not SEGMENT_RE.match(name):
            raise web.HTTPNotFound()
        path = os.path.join(self.recordings_dir, cam["id"], name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        headers = {"Content-Type": "video/mp4", "Cache-Control": "private, max-age=3600"}
        if request.query.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{cam["id"]}_{name}"'
        return web.FileResponse(path, headers=headers)

    # ---------- Live view (proxy ke go2rtc di localhost) ----------
    async def handle_snapshot(self, request):
        cam = self._camera(request)
        session = await self._http()
        try:
            async with session.get(f"{self.go2rtc_url}/api/frame.jpeg", params={"src": cam["live_stream"]},
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    raise web.HTTPBadGateway(text="Snapshot gagal")
                return web.Response(body=await r.read(), content_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text="go2rtc tidak merespon")

    async def handle_player_file(self, request):
        name = request.match_info.get("file", "")
        if name not in PLAYER_FILES:
            raise web.HTTPNotFound()
        if name not in self._player_cache:
            session = await self._http()
            async with session.get(f"{self.go2rtc_url}/{name}") as r:
                if r.status != 200:
                    raise web.HTTPBadGateway()
                self._player_cache[name] = await r.read()
        return web.Response(body=self._player_cache[name], content_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=86400"})

    async def handle_live_ws(self, request):
        src = request.query.get("src", "")
        if src not in self.allowed_streams:
            raise web.HTTPForbidden(text="Stream tidak diizinkan")

        client = web.WebSocketResponse(max_msg_size=0, heartbeat=30)
        await client.prepare(request)
        session = await self._http()
        try:
            async with session.ws_connect(f"{self.go2rtc_url}/api/ws", params={"src": src}, max_msg_size=0) as upstream:
                async def pipe(source, target):
                    async for msg in source:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await target.send_str(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await target.send_bytes(msg.data)
                        else:
                            break

                tasks = [asyncio.ensure_future(pipe(client, upstream)), asyncio.ensure_future(pipe(upstream, client))]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except (aiohttp.ClientError, ConnectionResetError) as e:
            print(f"[CCTV] Live stream '{src}' terputus: {e}")
        finally:
            await client.close()
        return client

    # ---------- Pan-tilt ----------
    async def handle_ptz(self, request):
        cam = self._camera(request)
        ptz = self.ptz.get(cam["id"])
        if not ptz:
            return web.json_response({"error": "Kamera tidak mendukung pan-tilt"}, status=400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        step = max(0.01, min(0.5, float(body.get("step", 0.1))))
        x, y = {"left": (-step, 0), "right": (step, 0), "up": (0, step), "down": (0, -step)}.get(body.get("direction"), (0, 0))
        if x == 0 and y == 0:
            return web.json_response({"error": "direction harus left/right/up/down"}, status=400)
        try:
            await asyncio.get_running_loop().run_in_executor(None, ptz.move, x, y)
        except Exception as e:
            ptz.profile_token = None
            return web.json_response({"error": f"PTZ gagal: {e}"}, status=502)
        return web.json_response({"status": "ok"})
