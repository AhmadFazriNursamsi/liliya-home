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
# Event ONVIF yang dicatat sebagai gerakan (Tapo: deteksi gerakan & deteksi orang)
MOTION_ITEMS = {"IsMotion": "motion", "IsPeople": "people"}
MOTION_MAX_SECONDS = 600   # gerakan yang tidak pernah "selesai" dipotong per 10 menit
MOTION_MERGE_GAP = 5       # kejadian yang berjarak < 5 detik digabung jadi satu tanda
PULL_BODY = ('<PullMessages xmlns="http://www.onvif.org/ver10/events/wsdl"><Timeout>PT30S</Timeout>'
             '<MessageLimit>20</MessageLimit></PullMessages>')


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

    def envelope(self, body: str) -> str:
        return f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">{self._security_header()}<s:Body>{body}</s:Body></s:Envelope>'

    def _call(self, body: str) -> str:
        r = requests.post(self.url, data=self.envelope(body), headers={"Content-Type": "application/soap+xml"}, timeout=5)
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
        # Catatan gerakan: {recordings_dir}/.motion/{cam}/YYYY-MM-DD.jsonl (folder tersembunyi,
        # jadi tidak ikut tersentuh cctv-cleanup / cctv-upload)
        self.motion_enabled = self.enabled and cfg.get("motion_events", True)
        self.motion_dir = os.path.join(self.recordings_dir, ".motion")
        self.motion_keep_days = int(cfg.get("retention_days", 3)) + 1
        self._motion_active = {}   # cam_id -> {kind: start_ts}
        self._motion_tasks = []
        self._motion_pruned = ""
        self.push = None           # PushManager (notifikasi FCM saat ada gerakan), di-set oleh WebServer
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
        app.on_startup.append(self._start_motion)
        app.on_cleanup.append(self._stop_motion)

    # ---------- Deteksi gerakan (event ONVIF dari kamera, tanpa analisis video di STB) ----------
    async def _start_motion(self, app):
        if not self.motion_enabled:
            return
        for cam_id, onvif in self.ptz.items():
            self._motion_tasks.append(asyncio.ensure_future(self._motion_watch(cam_id, onvif)))

    async def _stop_motion(self, app):
        for t in self._motion_tasks:
            t.cancel()
        now = time.time()
        for cam_id in list(self._motion_active):
            self._close_motion(cam_id, now)

    async def _onvif_post(self, onvif: OnvifPTZ, url: str, body: str, timeout: float = 10) -> str:
        session = await self._http()
        async with session.post(url, data=onvif.envelope(body), headers={"Content-Type": "application/soap+xml"},
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return text

    async def _motion_watch(self, cam_id: str, onvif: OnvifPTZ):
        address, renew_at, backoff, failing = None, 0, 5, False
        while True:
            try:
                if address is None:
                    xml = await self._onvif_post(onvif, onvif.url,
                        '<CreatePullPointSubscription xmlns="http://www.onvif.org/ver10/events/wsdl">'
                        '<InitialTerminationTime>PT600S</InitialTerminationTime></CreatePullPointSubscription>')
                    m = re.search(r"Address>\s*([^<\s]+)", xml)
                    if not m:
                        raise RuntimeError("alamat PullPoint tidak ditemukan")
                    address, renew_at = m.group(1), time.time() + 300
                    print(f"[CCTV] 👀 Deteksi gerakan '{cam_id}' aktif")
                elif time.time() > renew_at:
                    await self._onvif_post(onvif, address,
                        '<Renew xmlns="http://docs.oasis-open.org/wsn/b-2"><TerminationTime>PT600S</TerminationTime></Renew>')
                    renew_at = time.time() + 300
                xml = await self._onvif_post(onvif, address, PULL_BODY, timeout=45)
                self._handle_motion_xml(cam_id, xml)
                self._split_long_motion(cam_id)
                backoff, failing = 5, False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not failing:
                    print(f"[CCTV] Event gerakan '{cam_id}' terputus: {e!r}, mencoba lagi...")
                failing, address = True, None
                self._close_motion(cam_id, time.time())
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    def _handle_motion_xml(self, cam_id: str, xml: str):
        now = time.time()
        for msg in re.findall(r"NotificationMessage>(.*?)</[\w-]*:?NotificationMessage>", xml, re.S):
            data = re.search(r"Data>(.*?)</[\w-]*:?Data>", msg, re.S)
            for tag in re.findall(r"<[\w-]*:?SimpleItem\b[^>]*>", data.group(1) if data else ""):
                name = re.search(r'Name="(\w+)"', tag)
                value = re.search(r'Value="(\w+)"', tag)
                kind = MOTION_ITEMS.get(name.group(1)) if name else None
                if kind and value:
                    self._set_motion(cam_id, kind, value.group(1).lower() == "true", now)

    def _set_motion(self, cam_id: str, kind: str, active: bool, now: float):
        current = self._motion_active.setdefault(cam_id, {})
        if active and kind not in current:
            # Notifikasi saat gerakan baru dimulai, atau saat kamera mengenali ada orang
            if self.push and (not current or kind == "people"):
                cam = self.cameras.get(cam_id, {})
                asyncio.ensure_future(self.push.notify_motion(
                    cam_id, cam.get("name", cam_id), kind, now, self._snapshot_bytes(cam)))
            current[kind] = now
        elif not active and kind in current:
            self._write_motion(cam_id, kind, current.pop(kind), now)

    def _split_long_motion(self, cam_id: str):
        now = time.time()
        current = self._motion_active.get(cam_id, {})
        for kind, start in list(current.items()):
            if now - start > MOTION_MAX_SECONDS:
                self._write_motion(cam_id, kind, start, now)
                current[kind] = now

    def _close_motion(self, cam_id: str, now: float):
        for kind, start in list(self._motion_active.pop(cam_id, {}).items()):
            self._write_motion(cam_id, kind, start, now)

    def _write_motion(self, cam_id: str, kind: str, start: float, end: float):
        day = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d")
        folder = os.path.join(self.motion_dir, cam_id)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, day + ".jsonl"), "a") as f:
                f.write(json.dumps({"s": int(start), "e": max(int(end), int(start) + 1), "k": kind}) + "\n")
            if self._motion_pruned != day:
                self._motion_pruned = day
                limit = (datetime.date.today() - datetime.timedelta(days=self.motion_keep_days)).isoformat()
                for name in os.listdir(folder):
                    if name.endswith(".jsonl") and name[:10] < limit:
                        os.remove(os.path.join(folder, name))
        except OSError as e:
            print(f"[CCTV] Gagal menyimpan catatan gerakan: {e}")

    def _read_motion(self, cam_id: str, date: str):
        """Kejadian gerakan pada tanggal itu (+ hari sebelumnya untuk yang melewati tengah malam)."""
        events = []
        try:
            day = datetime.date.fromisoformat(date)
        except ValueError:
            return events
        for d in (day - datetime.timedelta(days=1), day):
            try:
                with open(os.path.join(self.motion_dir, cam_id, d.isoformat() + ".jsonl")) as f:
                    for line in f:
                        try:
                            ev = json.loads(line)
                            events.append((int(ev["s"]), int(ev["e"]), ev.get("k") == "people"))
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                continue
        now = int(time.time())
        for kind, start in self._motion_active.get(cam_id, {}).items():
            events.append((int(start), now, kind == "people"))
        # Gabungkan gerakan & orang yang bertumpuk / berdekatan menjadi satu tanda
        merged = []
        for s, e, people in sorted(events):
            if merged and s <= merged[-1][1] + MOTION_MERGE_GAP:
                merged[-1][1] = max(merged[-1][1], e)
                merged[-1][2] = merged[-1][2] or people
            else:
                merged.append([s, e, people])
        return merged

    @staticmethod
    def _attach_motion(segments, events):
        for seg in segments:
            start, end = seg["start_ts"], seg["end_ts"]
            seg["motion"] = [
                {"t": max(0, s - start), "d": max(1, min(e, end) - max(s, start)), "people": people}
                for s, e, people in events if s < end and e > start
            ]

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
        segments = [s for s in segments if s["date"] == date]
        events = await asyncio.get_running_loop().run_in_executor(None, self._read_motion, cam["id"], date) if date else []
        self._attach_motion(segments, events)
        return web.json_response({
            "camera": cam["id"],
            "dates": dates,
            "date": date,
            "motion_enabled": cam["id"] in self.ptz and self.motion_enabled,
            "segments": segments,
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
    async def _snapshot_bytes(self, cam: dict):
        if not cam:
            return None
        session = await self._http()
        try:
            async with session.get(f"{self.go2rtc_url}/api/frame.jpeg", params={"src": cam["live_stream"]},
                                   timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.read() if r.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

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
                    try:
                        async for msg in source:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await target.send_str(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await target.send_bytes(msg.data)
                            else:
                                break
                    except (aiohttp.ClientError, ConnectionResetError):
                        pass  # penonton menutup live view saat data masih dikirim

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
