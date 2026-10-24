import asyncio
import json
import os
import threading
from aiohttp import web

from core.web_auth import WebAuth
from core.cctv import CCTVManager
from core.router import RouterManager

class WebServer:
    def __init__(self, config, event_bridge=None, smart_home_db=None, host="0.0.0.0", port=8080):
        self.config = config
        self.host = host
        self.port = port
        self.event_bridge = event_bridge
        self.smart_home_db = smart_home_db
        self.websockets = set()
        self.loop = None
        self.thread = None
        
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.web_dir = os.path.join(self.base_dir, "web")

        self.auth = WebAuth(config, self.base_dir)
        self.cctv = CCTVManager(config)
        self.router = RouterManager(config, self.base_dir)
        self.app = web.Application(middlewares=[self.auth.middleware])
        self._setup_routes()

    def set_smart_home_db(self, smart_home_db):
        self.smart_home_db = smart_home_db

    def _setup_routes(self):
        # Login Routes
        self.app.router.add_get('/login', self.auth.handle_login_page)
        self.app.router.add_post('/login', self.auth.handle_login_submit)
        self.app.router.add_get('/logout', self.auth.handle_logout)

        # Frontend Routes
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/ws', self.handle_ws)
        self.app.router.add_static('/static/', path=os.path.join(self.web_dir, 'static'), name='static')
        
        # REST API Routes for ESP32 / IoT / Home Assistant / External Apps
        self.app.router.add_get('/api/devices', self.handle_api_get_devices)
        self.app.router.add_get('/api/device/{device_id}', self.handle_api_get_device)
        self.app.router.add_post('/api/device/{device_id}', self.handle_api_update_device)

        # CRUD perangkat (Tambah / Edit / Hapus dari Web UI & APK)
        self.app.router.add_post('/api/devices', self.handle_api_create_device)
        self.app.router.add_put('/api/devices/{device_id}', self.handle_api_edit_device)
        self.app.router.add_delete('/api/devices/{device_id}', self.handle_api_delete_device)
        self.app.router.add_get('/api/tuya/devices', self.handle_api_tuya_devices)

        # CCTV Routes (rekaman, live view via go2rtc, pan-tilt)
        self.cctv.register_routes(self.app)

        # Router Routes (perangkat terhubung, kunci IP, status internet, restart)
        self.router.register_routes(self.app)

    async def handle_index(self, request):
        index_file = os.path.join(self.web_dir, 'index.html')
        if os.path.exists(index_file):
            return web.FileResponse(index_file, headers={"Cache-Control": "no-store"})
        return web.Response(text="Web UI index.html not found", status=404)

    async def handle_api_get_devices(self, request):
        if self.smart_home_db:
            return web.json_response(self.smart_home_db.get_all_devices())
        return web.json_response([], status=200)

    async def handle_api_get_device(self, request):
        dev_id = request.match_info.get('device_id', '')
        if self.smart_home_db:
            dev = self.smart_home_db.get_device_by_id(dev_id)
            if dev:
                return web.json_response(dev)
        return web.json_response({"error": "Device not found"}, status=404)

    async def handle_api_update_device(self, request):
        dev_id = request.match_info.get('device_id', '')
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)
            
        if self.smart_home_db:
            updated = self.smart_home_db.update_device_state(dev_id, body)
            if updated:
                return web.json_response({"status": "success", "device": updated})
        return web.json_response({"error": "Device not found or DB not initialized"}, status=404)

    async def _json_body(self, request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Body JSON tidak valid"}), content_type="application/json")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Body harus berupa objek JSON"}), content_type="application/json")
        return body

    async def handle_api_create_device(self, request):
        if not self.smart_home_db:
            return web.json_response({"error": "Database perangkat belum siap"}, status=503)
        body = await self._json_body(request)
        try:
            device = self.smart_home_db.add_device(body)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        self._request_tuya_sync()
        return web.json_response({"status": "success", "device": device}, status=201)

    async def handle_api_edit_device(self, request):
        if not self.smart_home_db:
            return web.json_response({"error": "Database perangkat belum siap"}, status=503)
        body = await self._json_body(request)
        allowed = {k: body[k] for k in ("name", "room", "type", "aliases", "tuya_id") if k in body}
        try:
            device = self.smart_home_db.update_device_info(request.match_info["device_id"], allowed)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        if not device:
            return web.json_response({"error": "Perangkat tidak ditemukan"}, status=404)
        self._request_tuya_sync()
        return web.json_response({"status": "success", "device": device})

    async def handle_api_delete_device(self, request):
        if not self.smart_home_db or not self.smart_home_db.delete_device(request.match_info["device_id"]):
            return web.json_response({"error": "Perangkat tidak ditemukan"}, status=404)
        return web.json_response({"status": "success"})

    async def handle_api_tuya_devices(self, request):
        bridge = getattr(self.smart_home_db, "tuya_bridge", None) if self.smart_home_db else None
        if not bridge or not getattr(bridge, "cloud", None):
            return web.json_response({"enabled": False, "devices": []})
        try:
            devices = await asyncio.get_running_loop().run_in_executor(None, bridge.list_cloud_devices)
        except Exception as e:
            return web.json_response({"enabled": True, "error": str(e), "devices": []}, status=502)
        used = {d.get("tuya_id"): d.get("name") for d in self.smart_home_db.get_all_devices() if d.get("tuya_id")}
        for d in devices:
            d["used_by"] = used.get(d["tuya_id"])
        return web.json_response({"enabled": True, "devices": devices})

    def _request_tuya_sync(self):
        bridge = getattr(self.smart_home_db, "tuya_bridge", None) if self.smart_home_db else None
        if bridge and hasattr(bridge, "request_sync"):
            bridge.request_sync()

    async def handle_ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
        self._request_tuya_sync()
        print(f"[WebUI] 🌐 Client terhubung via WebSocket ({len(self.websockets)} aktif)")
        
        # Kirim status awal
        await ws.send_json({
            "type": "init",
            "status": "idle",
            "message": "STB Voice Assistant Online"
        })

        # Kirim sinkronisasi penuh perangkat smart home
        if self.smart_home_db:
            await ws.send_json({
                "type": "smart_home_sync",
                "devices": self.smart_home_db.get_all_devices()
            })

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    
                    if msg_type == "text_command" and self.event_bridge:
                        text = data.get("text", "")
                        self.event_bridge.on_web_text_command(text)
                    elif msg_type == "audio_command" and self.event_bridge:
                        audio_b64 = data.get("audio", "")
                        self.event_bridge.on_web_audio_command(audio_b64)
                    elif msg_type == "media_control" and self.event_bridge:
                        action = data.get("action")
                        self.event_bridge.on_web_media_action(action)
                    elif msg_type == "set_volume" and self.event_bridge:
                        volume = data.get("volume", 80)
                        self.event_bridge.on_web_set_volume(volume)
                    elif msg_type == "smart_home_toggle" and self.smart_home_db:
                        dev_id = data.get("id")
                        dev = self.smart_home_db.get_device_by_id(dev_id)
                        if dev:
                            new_st = "off" if dev.get("state") == "on" else "on"
                            self.smart_home_db.update_device_state(dev_id, {"state": new_st})
                    elif msg_type == "smart_home_set_state" and self.smart_home_db:
                        dev_id = data.get("id")
                        updates = data.get("updates", {})
                        self.smart_home_db.update_device_state(dev_id, updates)
        finally:
            self.websockets.discard(ws)
            print(f"[WebUI] 🔌 Client terputus ({len(self.websockets)} aktif)")
            
        return ws

    def broadcast(self, event_type: str, payload: dict):
        if not self.websockets or not self.loop:
            return
            
        message = json.dumps({"type": event_type, **payload})
        for ws in list(self.websockets):
            if not ws.closed:
                asyncio.run_coroutine_threadsafe(ws.send_str(message), self.loop)

    def start_background(self):
        def _run_server():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            runner = web.AppRunner(self.app)
            self.loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, self.host, self.port)
            self.loop.run_until_complete(site.start())
            print(f"[WebUI] 🖥️ Web Dashboard & Smart Display aktif di: http://{self.host}:{self.port}")
            self.loop.run_forever()

        self.thread = threading.Thread(target=_run_server, daemon=True)
        self.thread.start()
