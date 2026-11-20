"""Daftar radio Indonesia terpopuler dari Radio Browser (radio-browser.info) untuk tab Radio.

Urutan memakai clickcount Radio Browser (jumlah pendengar lewat direktori itu per hari), bukan rating resmi.
Hanya stasiun dari daftar ini yang bisa diputar lewat API, jadi web tidak bisa menyuruh STB membuka URL sembarang.
"""
import asyncio
import re
import time

import aiohttp
from aiohttp import web

MIRRORS = ("https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info",
           "https://fi1.api.radio-browser.info", "https://nl1.api.radio-browser.info")
USER_AGENT = "LiliyaHome/1.0 (+https://github.com/AhmadFazriNursamsi/liliya-home)"
CACHE_SECONDS = 6 * 3600
TOP_N = 20
# Siaran TV ikut terdaftar di direktori; tab ini khusus radio
TV_RE = re.compile(r"\btv\b|televisi", re.I)


class RadioManager:
    def __init__(self, get_media_player):
        self._get_media_player = get_media_player   # media player baru tersedia setelah EventBridge dibuat
        self._stations = []
        self._fetched_at = 0
        self._lock = asyncio.Lock()

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/radio/top", self.handle_top)
        app.router.add_post("/api/radio/play", self.handle_play)
        app.router.add_post("/api/radio/stop", self.handle_stop)

    async def _fetch(self, session, path):
        last = None
        for base in MIRRORS:
            try:
                async with session.get(base + path, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        return await r.json(content_type=None)
                    last = f"HTTP {r.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
        raise RuntimeError(f"Radio Browser tidak bisa dihubungi ({last})")

    async def top(self, force=False):
        async with self._lock:
            if self._stations and not force and time.time() - self._fetched_at < CACHE_SECONDS:
                return self._stations
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
                raw = await self._fetch(s, "/json/stations/search?countrycode=ID&order=clickcount&reverse=true"
                                           "&hidebroken=true&limit=80")
            stations, seen = [], set()
            for st in raw:
                name = re.sub(r"\s+", " ", (st.get("name") or "")).strip()
                url = (st.get("url_resolved") or st.get("url") or "").strip()
                key = re.sub(r"[^a-z0-9]", "", name.lower())
                # Stasiun yang sama sering terdaftar dua kali dengan nama/host berbeda tapi ID stream sama
                tail = url.rstrip("/").rsplit("/", 1)[-1]
                stream_key = tail if len(tail) >= 10 and "chunklist" not in tail else url
                if not name or not key or key in seen or stream_key in seen or not url.lower().startswith(("http://", "https://")):
                    continue
                # Siaran TV: nama/tag berisi "TV", atau bitrate setinggi video (CNN Indonesia 384 kbps)
                if TV_RE.search(name) or TV_RE.search(st.get("tags") or "") or (st.get("bitrate") or 0) >= 384:
                    continue
                seen.update((key, stream_key))
                tags = [t.strip() for t in (st.get("tags") or "").split(",") if t.strip() and t.strip() != "undefined"][:3]
                stations.append({
                    "uuid": st.get("stationuuid"),
                    "name": name,
                    "url": url,
                    "favicon": st.get("favicon") or "",
                    "place": st.get("state") or "",
                    "tags": tags,
                    "codec": st.get("codec") or "",
                    "bitrate": st.get("bitrate") or 0,
                    "clicks": st.get("clickcount") or 0,
                })
                if len(stations) >= TOP_N:
                    break
            if stations:
                self._stations, self._fetched_at = stations, time.time()
            return self._stations

    async def _count_click(self, uuid):
        """Radio Browser meminta klien melaporkan stasiun yang diputar (dipakai untuk urutan populer)."""
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
                await self._fetch(s, f"/json/url/{uuid}")
        except Exception:
            pass

    async def handle_top(self, request):
        try:
            stations = await self.top(force=request.query.get("refresh") == "1")
        except Exception as e:
            if not self._stations:
                return web.json_response({"error": f"Gagal memuat daftar radio: {e}"}, status=502)
            stations = self._stations
        mp = self._get_media_player()
        playing = mp.current_title if mp and mp.is_playing() else ""
        public = [{k: v for k, v in s.items() if k != "url"} for s in stations]
        return web.json_response({"stations": public, "playing": playing, "updated": self._fetched_at})

    async def handle_play(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        uuid = str((body or {}).get("uuid", ""))
        station = next((s for s in self._stations if s["uuid"] == uuid), None)
        if not station:
            return web.json_response({"error": "Radio tidak ada di daftar. Muat ulang daftar radio."}, status=404)
        mp = self._get_media_player()
        if not mp:
            return web.json_response({"error": "Pemutar audio belum siap"}, status=503)
        await asyncio.get_running_loop().run_in_executor(None, mp.play_stream, station["url"], station["name"])
        asyncio.ensure_future(self._count_click(uuid))
        print(f"[Radio] 📻 Diputar dari tab Radio: {station['name']}")
        return web.json_response({"status": "ok", "playing": station["name"]})

    async def handle_stop(self, request):
        mp = self._get_media_player()
        if mp:
            await asyncio.get_running_loop().run_in_executor(None, mp.stop_music)
        return web.json_response({"status": "ok"})
