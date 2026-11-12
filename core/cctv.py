import asyncio
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import time

import aiohttp
import requests
from aiohttp import web

from core.detector import LABELS, ObjectDetector

SEGMENT_RE = re.compile(r"^(\d{8})_(\d{6})\.mp4$")
# File statis player go2rtc yang boleh diambil lewat BUDI (API konfigurasi go2rtc TIDAK diekspos)
PLAYER_FILES = {"video-stream.js", "video-rtc.js"}
# Penyesuaian player go2rtc. Bawaannya: playbackRate = jarak ke ujung buffer, sehingga lewat internet
# yang datang bergelombang video terus melambat/mempercepat (gambar terlihat "goyang"). Diganti dengan
# kecepatan 1x dan koreksi kecil hanya bila tertinggal jauh / buffer hampir habis.
PLAYER_VERSION = "2"
PLAYER_PATCHES = {
    "video-rtc.js": [(
        "this.video.playbackRate = gap > 0.1 ? gap : 0.1;",
        "if (gap > 3) { this.video.currentTime = end - 0.7; }"
        " const rate = gap > 1.5 ? 1.15 : (gap < 0.25 ? 0.9 : 1.0);"
        " if (this.video.playbackRate !== rate) this.video.playbackRate = rate;",
    )],
    "video-stream.js": [("from './video-rtc.js'", f"from './video-rtc.js?v={PLAYER_VERSION}'")],
}
# Event ONVIF yang dicatat sebagai gerakan (Tapo: deteksi gerakan & deteksi orang)
MOTION_ITEMS = {"IsMotion": "motion", "IsPeople": "people"}
MOTION_MAX_SECONDS = 600   # gerakan yang tidak pernah "selesai" dipotong per 10 menit
MOTION_MERGE_GAP = 5       # kejadian yang berjarak < 5 detik digabung jadi satu tanda
DETECTION_MERGE_GAP = 60   # deteksi berlabel sama yang berjarak < 60 dtk = satu kejadian di riwayat
TRACK_BEFORE, TRACK_AFTER = 4, 14   # potongan rekaman yang dilacak: 4 dtk sebelum s/d 14 dtk setelah kejadian
TRACK_MAX_SECONDS = 90
CPU_TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"
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
                # zona yang diabaikan deteksi objek, [x1, y1, x2, y2] 0..1 (mis. jemuran yang tertiup angin)
                "ignore_zones": [tuple(float(v) for v in z) for z in (cam.get("ignore_zones") or []) if len(z) == 4],
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
        # Deteksi orang/hewan saat ada gerakan (stream SD lewat RTSP go2rtc); notifikasi hanya bila ada objek
        self.rtsp_base = cfg.get("go2rtc_rtsp", "rtsp://127.0.0.1:8554").rstrip("/")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.detector = ObjectDetector(config, base_dir) if self.motion_enabled else None
        self._detect_tasks = {}    # cam_id -> asyncio.Task
        # Riwayat deteksi: {recordings_dir}/.detections/{cam}/YYYY-MM-DD/{ts}.jpg + index.jsonl (foto berkotak)
        self.det_dir = os.path.join(self.recordings_dir, ".detections")
        self.det_keep_days = int((cfg.get("detection") or {}).get("history_days", 30))
        self._det_pruned = ""
        # Pengaman suhu: pelacakan rekaman ditunda saat STB >= batas, lanjut setelah turun 3 derajat
        self.track_max_temp = float((cfg.get("detection") or {}).get("track_max_temp", 77))
        if self.enabled:
            print(f"[CCTV] 📹 {len(self.cameras)} kamera aktif, rekaman di {self.recordings_dir}")

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/cctv/cameras", self.handle_cameras)
        app.router.add_get("/api/cctv/backup", self.handle_backup)
        app.router.add_get("/api/cctv/{cam}/recordings", self.handle_recordings)
        app.router.add_get("/api/cctv/{cam}/snapshot", self.handle_snapshot)
        app.router.add_get("/api/cctv/{cam}/detections", self.handle_detections)
        app.router.add_get("/cctv/det/{cam}/{date}/{file}", self.handle_detection_image)
        app.router.add_get("/api/cctv/{cam}/tracks/{file}", self.handle_tracks)
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
        if self.detector and self.detector.enabled:
            self._motion_tasks.append(asyncio.ensure_future(self._track_loop()))

    async def _stop_motion(self, app):
        for t in self._motion_tasks + list(self._detect_tasks.values()):
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
                    cand = m.group(1)
                    from urllib.parse import urlsplit
                    if urlsplit(cand).hostname != urlsplit(onvif.url).hostname:
                        # Kamera tidak boleh mengarahkan POST ke host lain (SSRF ke go2rtc dll.)
                        cand = urlsplit(onvif.url)._replace(path=urlsplit(cand).path, query=urlsplit(cand).query).geturl()
                    address, renew_at = cand, time.time() + 300
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
            if self.detector and self.detector.enabled:
                # Notifikasi menunggu hasil deteksi objek (bayangan/daun/jemuran tidak dikirim)
                task = self._detect_tasks.get(cam_id)
                if task is None or task.done():
                    self._detect_tasks[cam_id] = asyncio.ensure_future(self._detect_loop(cam_id))
            # Tanpa deteksi objek: notifikasi saat gerakan baru dimulai, atau saat kamera mengenali ada orang
            elif self.push and (not current or kind == "people"):
                cam = self.cameras.get(cam_id, {})
                asyncio.ensure_future(self.push.notify_motion(
                    cam_id, cam.get("name", cam_id), kind, now, lambda: self._snapshot_bytes(cam)))
            current[kind] = now
        elif not active and kind in current:
            self._write_motion(cam_id, kind, current.pop(kind), now)

    async def _detect_loop(self, cam_id: str):
        """Selama gerakan berlangsung: periksa beberapa detik stream SD, lalu jeda (makin lama bila kosong)."""
        cam = self.cameras.get(cam_id, {})
        url = f"{self.rtsp_base}/{cam.get('live_stream', cam_id)}"
        notified, gap = set(), 0
        while True:
            start = time.time()
            try:
                # batas waktu pengaman: analisis yang macet tidak boleh menghentikan deteksi selamanya
                res = await asyncio.wait_for(
                    self.detector.analyze(url, cam.get("ignore_zones", ())), self.detector.window_s + 30)
            except Exception as e:
                print(f"[Detector] Analisis '{cam_id}' gagal: {e!r}")
                return
            labels = res["labels"]
            if labels:
                self._write_motion(cam_id, "object", start, time.time(), labels)
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_detection, cam_id, start, labels, res.get("scores") or {}, res.get("jpeg"))
                new = [k for k in labels if k not in notified]
                if new:
                    notified.update(new)
                    names = ", ".join(LABELS[k][0] for k in labels)
                    print(f"[Detector] 🎯 '{cam_id}': {names} {res['scores']}")
                    if self.push:
                        jpeg = res.get("jpeg")

                        async def _snap(jpeg=jpeg):
                            return jpeg
                        name, emoji = LABELS[new[0]]
                        title = f"{emoji} {', '.join(LABELS[k][0] for k in new)} terdeteksi"
                        asyncio.ensure_future(self.push.notify_motion(
                            cam_id, cam.get("name", cam_id), new[0], start, _snap, title=title))
            gap = 20 if labels else min(60, gap * 2 or 10)
            for _ in range(gap):
                if not self._motion_active.get(cam_id):
                    return
                await asyncio.sleep(1)

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

    def _write_motion(self, cam_id: str, kind: str, start: float, end: float, objects=None):
        day = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d")
        folder = os.path.join(self.motion_dir, cam_id)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, day + ".jsonl"), "a") as f:
                rec = {"s": int(start), "e": max(int(end), int(start) + 1), "k": kind}
                if objects:
                    rec["o"] = list(objects)
                f.write(json.dumps(rec) + "\n")
            if self._motion_pruned != day:
                self._motion_pruned = day
                limit = (datetime.date.today() - datetime.timedelta(days=self.motion_keep_days)).isoformat()
                for name in os.listdir(folder):
                    if name.endswith(".jsonl") and name[:10] < limit:
                        os.remove(os.path.join(folder, name))
        except OSError as e:
            print(f"[CCTV] Gagal menyimpan catatan gerakan: {e}")

    # ---------- Riwayat deteksi orang/hewan (foto berkotak) ----------
    def _save_detection(self, cam_id: str, start: float, labels, scores: dict, jpeg):
        day = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d")
        folder = os.path.join(self.det_dir, cam_id, day)
        ts = int(start)
        try:
            os.makedirs(folder, exist_ok=True)
            if jpeg:
                with open(os.path.join(folder, f"{ts}.jpg"), "wb") as f:
                    f.write(jpeg)
            with open(os.path.join(folder, "index.jsonl"), "a") as f:
                f.write(json.dumps({"t": ts, "o": list(labels),
                                    "sc": {k: float(v) for k, v in scores.items()}, "img": bool(jpeg)}) + "\n")
            if self._det_pruned != day:
                self._det_pruned = day
                limit = (datetime.date.today() - datetime.timedelta(days=self.det_keep_days)).isoformat()
                for name in os.listdir(os.path.join(self.det_dir, cam_id)):
                    if len(name) == 10 and name < limit:
                        shutil.rmtree(os.path.join(self.det_dir, cam_id, name), ignore_errors=True)
        except OSError as e:
            print(f"[CCTV] Gagal menyimpan riwayat deteksi: {e}")

    def _read_detections(self, cam_id: str, date: str):
        """Deteksi pada tanggal itu, terbaru dulu. Deteksi berturut-turut (orang yang lama berdiri di depan
        kamera dicek tiap ~20 dtk) dengan label sama digabung menjadi satu kejadian."""
        items = []
        try:
            with open(os.path.join(self.det_dir, cam_id, date, "index.jsonl")) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        items.append((int(d["t"]), list(d.get("o") or []), d.get("sc") or {}, bool(d.get("img"))))
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            return []
        events = []
        for t, labels, scores, img in sorted(items):
            last = events[-1] if events else None
            if last and t - last["end"] <= DETECTION_MERGE_GAP and set(labels) <= set(last["keys"]):
                last["end"], last["count"] = t, last["count"] + 1
                continue
            if last and t - last["end"] <= DETECTION_MERGE_GAP and set(labels) > set(last["keys"]):
                # objek baru ikut muncul: perbarui kejadian yang sama dengan foto terbarunya
                last.update(keys=sorted(set(labels) | set(last["keys"]), key=lambda k: (k != "person", k)), end=t, count=last["count"] + 1,
                            img=f"{t}.jpg" if img else last["img"])
                continue
            events.append({"t": t, "end": t, "count": 1, "keys": list(labels), "scores": scores,
                           "img": f"{t}.jpg" if img else None})
        for ev in events:
            ev["objs"] = [{"k": k, "name": LABELS[k][0], "emoji": LABELS[k][1]} for k in ev.pop("keys") if k in LABELS]
            if ev["img"]:
                ev["img"] = f"/cctv/det/{cam_id}/{date}/{ev['img']}"
        return events[::-1]

    # ---------- Kotak bergerak di rekaman: deteksi ulang potongan rekaman di sekitar tiap kejadian ----------
    # Hasil per segmen: {det_dir}/{cam}/tracks/{nama segmen}.json = {"done": [t kejadian], "frames": [[detik, kotak]]}
    def _track_path(self, cam_id: str, seg_file: str) -> str:
        return os.path.join(self.det_dir, cam_id, "tracks", seg_file.replace(".mp4", ".json"))

    def _load_track(self, cam_id: str, seg_file: str) -> dict:
        try:
            with open(self._track_path(cam_id, seg_file)) as f:
                data = json.load(f)
                return {"done": list(data.get("done") or []), "frames": list(data.get("frames") or [])}
        except (OSError, ValueError):
            return {"done": [], "frames": []}

    def _save_track(self, cam_id: str, seg_file: str, data: dict):
        path = self._track_path(cam_id, seg_file)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        # rekaman hanya disimpan beberapa hari; hasil pelacakan lama ikut dihapus
        folder = os.path.dirname(path)
        limit = time.time() - (self.motion_keep_days + 3) * 86400
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            try:
                if os.path.getmtime(p) < limit:
                    os.remove(p)
            except OSError:
                pass

    def _track_jobs(self, cam_id: str, ev: dict, segments):
        """Potongan rekaman (segmen, detik awal, durasi) yang perlu dilacak untuk satu kejadian."""
        lo, hi = ev["t"] - TRACK_BEFORE, ev["end"] + TRACK_AFTER
        jobs = []
        for seg in segments:
            if seg["end_ts"] <= lo or seg["start_ts"] >= hi:
                continue
            start = max(0, lo - seg["start_ts"])
            dur = min(min(seg["end_ts"], hi) - seg["start_ts"] - start, TRACK_MAX_SECONDS)
            if dur >= 1:
                jobs.append((seg, start, dur))
        return jobs

    @staticmethod
    def _cpu_temp():
        try:
            with open(CPU_TEMP_PATH) as f:
                return int(f.read().strip()) / 1000
        except (OSError, ValueError):
            return None

    async def _wait_cool(self):
        t = self._cpu_temp()
        if t is None or t < self.track_max_temp:
            return
        print(f"[CCTV] 🌡️ Suhu STB {t:.0f}°C: pelacakan rekaman dijeda sampai < {self.track_max_temp - 3:.0f}°C")
        start = time.time()
        while (t := self._cpu_temp()) is not None and t >= self.track_max_temp - 3:
            await asyncio.sleep(15)
        print(f"[CCTV] 🌡️ Suhu STB {t if t is not None else 0:.0f}°C: pelacakan dilanjutkan (jeda {time.time() - start:.0f} dtk)")

    def _busy_detecting(self) -> bool:
        return any(not t.done() for t in self._detect_tasks.values())

    async def _track_loop(self):
        await asyncio.sleep(90)
        while True:
            try:
                await self._track_pending()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[CCTV] Pelacakan rekaman gagal: {e!r}")
            await asyncio.sleep(60)

    async def _track_pending(self):
        loop = asyncio.get_running_loop()
        today = datetime.date.today()
        for cam_id, cam in self.cameras.items():
            segments = await loop.run_in_executor(None, self._list_segments, cam_id)
            for day in (today, today - datetime.timedelta(days=1)):
                events = await loop.run_in_executor(None, self._read_detections, cam_id, day.isoformat())
                for ev in events:                                   # terbaru dulu
                    if time.time() - ev["end"] < 30:
                        continue                                    # kejadian masih berlangsung
                    for seg, start, dur in self._track_jobs(cam_id, ev, segments):
                        if seg["recording"]:
                            continue                                # tunggu rekaman 10 menit ini selesai
                        track = await loop.run_in_executor(None, self._load_track, cam_id, seg["file"])
                        if ev["t"] in track["done"]:
                            continue
                        while self._busy_detecting():               # beri jalan deteksi langsung lebih dulu
                            await asyncio.sleep(10)
                        await self._wait_cool()
                        t0 = time.time()
                        frames = await self.detector.track(
                            os.path.join(self.recordings_dir, cam_id, seg["file"]), start, dur,
                            [o["k"] for o in ev["objs"]], cam.get("ignore_zones", ()), cool_down=self._wait_cool)
                        merged = {f[0]: f for f in track["frames"]}
                        merged.update({f[0]: f for f in frames})
                        track["frames"] = [merged[k] for k in sorted(merged)]
                        track["done"].append(ev["t"])
                        await loop.run_in_executor(None, self._save_track, cam_id, seg["file"], track)
                        boxes = sum(1 for f in frames if f[1])
                        print(f"[CCTV] 🎞️ Kotak rekaman {seg['file']} @{int(start)}s: "
                              f"{boxes}/{len(frames)} frame berisi objek ({time.time() - t0:.0f} dtk)")

    async def handle_tracks(self, request):
        cam = self._camera(request)
        name = request.match_info.get("file", "")
        m = SEGMENT_RE.match(name)
        if not m:
            raise web.HTTPNotFound()
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, self._load_track, cam["id"], name)
        # masih ada kejadian di segmen ini yang belum dilacak?
        pending = False
        if self.detector and self.detector.enabled:
            seg = next((s for s in await loop.run_in_executor(None, self._list_segments, cam["id"]) if s["file"] == name), None)
            if seg:
                date = seg["date"]
                events = await loop.run_in_executor(None, self._read_detections, cam["id"], date)
                pending = any(ev["t"] not in track["done"] and self._track_jobs(cam["id"], ev, [seg]) for ev in events)
        return web.json_response({"frames": track["frames"], "pending": pending,
                                  "labels": {k: list(v) for k, v in LABELS.items()}},
                                  headers={"Cache-Control": "no-store"})

    async def handle_detections(self, request):
        cam = self._camera(request)
        date = request.query.get("date") or datetime.date.today().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise web.HTTPBadRequest(text="Tanggal tidak valid")
        events = await asyncio.get_running_loop().run_in_executor(None, self._read_detections, cam["id"], date)
        return web.json_response({"camera": cam["id"], "date": date,
                                  "enabled": bool(self.detector and self.detector.enabled), "events": events})

    async def handle_detection_image(self, request):
        cam = self._camera(request)
        date, name = request.match_info.get("date", ""), request.match_info.get("file", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re.fullmatch(r"\d{9,11}\.jpg", name):
            raise web.HTTPNotFound()
        path = os.path.join(self.det_dir, cam["id"], date, name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400"})

    def _read_motion(self, cam_id: str, date: str):
        """Kejadian gerakan pada tanggal itu (+ hari sebelumnya untuk yang melewati tengah malam)."""
        events = []
        detecting = bool(self.detector and self.detector.enabled)
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
                            objs = set(ev.get("o") or [])
                            # Deteksi orang dari kamera sering salah; bila deteksi objek STB aktif, pakai hasil STB
                            people = "person" in objs or (ev.get("k") == "people" and not detecting)
                            events.append((int(ev["s"]), int(ev["e"]), people, objs))
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                continue
        now = int(time.time())
        for kind, start in self._motion_active.get(cam_id, {}).items():
            events.append((int(start), now, kind == "people" and not detecting, set()))
        # Gabungkan gerakan, orang & objek yang bertumpuk / berdekatan menjadi satu tanda
        merged = []
        for s, e, people, objs in sorted(events, key=lambda ev: ev[:2]):
            if merged and s <= merged[-1][1] + MOTION_MERGE_GAP:
                merged[-1][1] = max(merged[-1][1], e)
                merged[-1][2] = merged[-1][2] or people
                merged[-1][3] |= objs
            else:
                merged.append([s, e, people, set(objs)])
        return merged

    @staticmethod
    def _attach_motion(segments, events):
        for seg in segments:
            start, end = seg["start_ts"], seg["end_ts"]
            seg["motion"] = [
                {"t": max(0, s - start), "d": max(1, min(e, end) - max(s, start)), "people": people,
                 "objs": [{"k": k, "name": LABELS[k][0], "emoji": LABELS[k][1]} for k in sorted(objs) if k in LABELS]}
                for s, e, people, objs in events if s < end and e > start
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
            "detection_enabled": bool(self.detector and self.detector.enabled),
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
                body = (await r.read()).decode("utf-8")
            for old, new in PLAYER_PATCHES.get(name, []):
                if old in body:
                    body = body.replace(old, new)
                else:
                    print(f"[CCTV] Penyesuaian player {name} dilewati (versi go2rtc berbeda)")
            self._player_cache[name] = body.encode("utf-8")
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
