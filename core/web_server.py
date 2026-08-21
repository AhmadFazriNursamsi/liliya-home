import asyncio
import json
import os
import threading
from aiohttp import web

class WebServer:
    def __init__(self, config, event_bridge=None, smart_home_db=None, host="0.0.0.0", port=8080):
        self.config = config
        self.host = host
        self.port = port
        self.event_bridge = event_bridge
        self.smart_home_db = smart_home_db
        self.app = web.Application()
        self.websockets = set()
        self.loop = None
        self.thread = None
        
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.web_dir = os.path.join(self.base_dir, "web")
        
        self._setup_routes()

    def set_smart_home_db(self, smart_home_db):
        self.smart_home_db = smart_home_db

    def _setup_routes(self):
        # Frontend Routes
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/ws', self.handle_ws)
        self.app.router.add_static('/static/', path=os.path.join(self.web_dir, 'static'), name='static')
        
        # REST API Routes for ESP32 / IoT / Home Assistant / External Apps
        self.app.router.add_get('/api/devices', self.handle_api_get_devices)
        self.app.router.add_get('/api/device/{device_id}', self.handle_api_get_device)
        self.app.router.add_post('/api/device/{device_id}', self.handle_api_update_device)

    async def handle_index(self, request):
        index_file = os.path.join(self.web_dir, 'index.html')
        if os.path.exists(index_file):
            return web.FileResponse(index_file)
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

    async def handle_ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
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
