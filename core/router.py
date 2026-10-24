import asyncio
import html
import json
import os
import re
import socket
import threading
import time
from contextlib import contextmanager

import requests
from aiohttp import web

# Router ZTE (CL601 / seri F6xx) hanya mengizinkan SATU sesi admin sekaligus dan mengenali sesi dari IP.
# Karena itu setiap aksi: login -> ambil/simpan data -> logout, secepat mungkin, agar pemilik rumah
# tetap bisa membuka halaman admin router dari browser.
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
DEVICE_ICONS = {"phone", "laptop", "tv", "camera", "server", "smart", "router", "other"}


class RouterError(Exception):
    pass


def _unescape(value: str) -> str:
    """Nilai di halaman ZTE ditulis sebagai \\x2e dan/atau entitas HTML &#46;."""
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
    return html.unescape(value).strip()


def _transfer_values(page: str) -> list:
    """Semua pasangan Transfer_meaning('Nama','nilai') sesuai urutan di halaman."""
    return [(k, _unescape(v)) for k, v in re.findall(r"Transfer_meaning\('([^']+)','([^']*)'\)", page)]


def _cell(page: str, cell_id: str) -> str:
    m = re.search(rf'id="{cell_id}"[^>]*>(.*?)</td>', page, re.S)
    return _unescape(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""


def _is_random_mac(mac: str) -> bool:
    """MAC privasi (acak) Android/iOS: bit 'locally administered' menyala."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


class ZteRouter:
    def __init__(self, host: str, username: str, password: str):
        self.base = f"http://{host}"
        self.username = username
        self.password = str(password)
        self._lock = threading.Lock()

    @contextmanager
    def session(self):
        with self._lock:
            s = requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0 Liliya"
            try:
                login_page = s.get(self.base + "/", timeout=8).text
                if "Another user has logged in" in login_page:
                    raise RouterError("Router sedang dibuka di perangkat lain. Logout dulu dari halaman admin router, lalu coba lagi.")
                token = re.search(r'Frm_Logintoken"\)\.value = "(\d+)"', login_page)
                r = s.post(self.base + "/", timeout=8, allow_redirects=False, data={
                    "frashnum": "", "action": "login", "Frm_Logintoken": token.group(1) if token else "0",
                    "Username": self.username, "Password": self.password,
                })
                if r.status_code != 302:
                    if "three times" in r.text:
                        raise RouterError("Login router dikunci 1 menit karena salah password 3 kali.")
                    raise RouterError("Login router gagal. Periksa username/password router di config.yaml.")
            except requests.RequestException as e:
                raise RouterError(f"Router tidak bisa dihubungi: {e.__class__.__name__}")
            try:
                yield s
            finally:
                try:
                    s.post(self.base + "/", data={"logout": "1"}, timeout=5)
                except requests.RequestException:
                    pass
                s.close()

    def _page(self, s, name: str) -> str:
        r = s.get(f"{self.base}/getpage.gch?pid=1002&nextpage={name}.gch", timeout=15)
        r.raise_for_status()
        return r.text

    # ---------- Baca ----------
    def _parse_clients(self, page: str) -> list:
        clients, current = {}, {}
        for key, value in _transfer_values(page):
            m = re.fullmatch(r"(HostName|MACAddr|IPAddr|ExpiredTime|PhyPortName)(\d+)", key)
            if m:
                clients.setdefault(int(m.group(2)), {})[m.group(1)] = value
        result = []
        for _, c in sorted(clients.items()):
            mac = c.get("MACAddr", "").upper()
            if not MAC_RE.match(mac):
                continue
            port = c.get("PhyPortName", "")
            result.append({
                "mac": mac,
                "ip": c.get("IPAddr", ""),
                "hostname": c.get("HostName", ""),
                "port": port,
                "connection": "wifi" if port.upper().startswith("SSID") else "lan",
                "lease_minutes": int(c["ExpiredTime"]) if c.get("ExpiredTime", "").isdigit() else None,
                "random_mac": _is_random_mac(mac),
            })
        return result

    def _parse_reservations(self, page: str) -> list:
        entries = {}
        for key, value in _transfer_values(page):
            m = re.fullmatch(r"(IPAddr|MACAddr)(\d+)", key)
            if m:
                entries.setdefault(int(m.group(2)), {})[m.group(1)] = value
        return [{"index": i, "ip": e.get("IPAddr", ""), "mac": e.get("MACAddr", "").upper()}
                for i, e in sorted(entries.items()) if e.get("IPAddr")]

    def _parse_pool(self, page: str) -> dict:
        values = dict(v for v in _transfer_values(page) if v[1])
        return {"start": values.get("MinAddress", ""), "end": values.get("MaxAddress", ""),
                "gateway": values.get("IPRouters", ""), "lease_minutes": values.get("LeaseTime", "")}

    def _parse_wan(self, page: str) -> dict:
        text = re.sub(r"<script.*?</script>", "", page, flags=re.S)
        cells = [_unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", text, re.S)]
        blocks, block = [], None
        for label, value in zip(cells, cells[1:]):
            if label == "Type":
                block = {}
                blocks.append(block)
            if block is not None and label in ("Type", "Connection Name", "IP", "Connection Status", "Online Duration", "Gateway"):
                block.setdefault(label, value)
        internet = next((b for b in blocks if "INTERNET" in b.get("Connection Name", "").upper()), blocks[0] if blocks else {})
        duration = re.match(r"(\d+)", internet.get("Online Duration", ""))
        return {
            "connected": internet.get("Connection Status", "").lower() == "connected",
            "status": internet.get("Connection Status", ""),
            "type": internet.get("Type", ""),
            "ip": internet.get("IP", ""),
            "online_seconds": int(duration.group(1)) if duration else None,
        }

    def _parse_optical(self, page: str) -> dict:
        def num(name):
            m = re.search(rf'var {name} = "(-?\d+)"', page)
            return round(int(m.group(1)) / 10000, 1) if m else None
        rx = num("RxPower")
        quality = None
        if rx is not None:
            quality = "baik" if -25 <= rx <= -8 else ("lemah" if rx < -25 else "terlalu kuat")
        return {"rx_dbm": rx, "tx_dbm": num("TxPower"), "quality": quality}

    def _parse_blocklist(self, page: str) -> dict:
        values, rules = {}, {}
        for key, value in _transfer_values(page):
            m = re.fullmatch(r"(SrcMacAddr|Enable)(\d+)", key)
            if m:
                rules.setdefault(int(m.group(2)), {})[m.group(1)] = value
            elif value:
                values[key] = value
        return {
            "enabled": values.get("MacFilterEnable") == "1",
            "mode": values.get("MacFilterTarget", "Discard"),
            "basic": {k: values.get(k, "NULL") for k in ("IsWanSrvCntl", "IpFilterTarget", "UrlFilterTarget", "UrlFilterEnable",
                                                         "SrvCntlTarget", "DefaultPolicy", "MacFilterTarget", "MacFilterEnable")},
            "rules": [{"index": i, "mac": r.get("SrcMacAddr", "").upper()} for i, r in sorted(rules.items())
                      if MAC_RE.match(r.get("SrcMacAddr", "").upper()) and r.get("SrcMacAddr") != "00:00:00:00:00:00"],
        }

    def snapshot(self) -> dict:
        with self.session() as s:
            dhcp = self._page(s, "net_dhcp_dynamic_t")
            static = self._page(s, "net_dhcp_static_t")
            blocklist = self._page(s, "sec_macfilter_conf_t")
            wan = self._page(s, "status_ethwan_if_t")
            optical = self._page(s, "gpon_status_link_t")
            info = self._page(s, "status_dev_info_t")
        return {
            "clients": self._parse_clients(dhcp),
            "pool": self._parse_pool(dhcp),
            "reservations": self._parse_reservations(static),
            "blocked": [r["mac"] for r in self._parse_blocklist(blocklist)["rules"]],
            "wan": self._parse_wan(wan),
            "optical": self._parse_optical(optical),
            "device": {"model": _cell(info, "Frm_ModelName"), "serial": _cell(info, "Frm_SerialNumber"),
                       "software": _cell(info, "Frm_SoftwareVer")},
            "fetched_at": int(time.time()),
        }

    # ---------- Ubah ----------
    def _submit(self, s, page_name: str, page: str, fields: dict) -> str:
        token = re.search(r'var session_token = "(\d+)"', page)
        data = {"IF_ERRORSTR": "SUCC", "IF_ERRORPARAM": "SUCC", "IF_ERRORTYPE": "-1", **fields}
        if token:
            data["_SESSION_TOKEN"] = token.group(1)
        r = s.post(f"{self.base}/getpage.gch?pid=1002&nextpage={page_name}.gch", data=data, timeout=20)
        r.raise_for_status()
        err = dict(_transfer_values(r.text)).get("IF_ERRORSTR", "SUCC")
        if err not in ("SUCC", ""):
            raise RouterError(f"Router menolak perubahan ({err}).")
        return r.text

    def add_reservation(self, mac: str, ip: str) -> list:
        with self.session() as s:
            page = self._page(s, "net_dhcp_static_t")
            current = self._parse_reservations(page)
            if any(r["mac"] == mac for r in current):
                raise RouterError("Perangkat ini sudah punya IP terkunci.")
            if any(r["ip"] == ip for r in current):
                raise RouterError(f"IP {ip} sudah dikunci untuk perangkat lain.")
            result = self._submit(s, "net_dhcp_static_t", page, {
                "ViewName": "NULL", "HostCfgViewName": "NULL", "IPAddr": ip, "MACAddr": mac,
                "IF_ACTION": "new", "IF_INDEX": "-1", "IF_INSTNUM": str(len(current)),
            })
            saved = self._parse_reservations(result) or self._parse_reservations(self._page(s, "net_dhcp_static_t"))
        if not any(r["mac"] == mac and r["ip"] == ip for r in saved):
            raise RouterError("Router tidak menyimpan kunci IP. Coba lagi.")
        return saved

    def delete_reservation(self, mac: str) -> list:
        with self.session() as s:
            page = self._page(s, "net_dhcp_static_t")
            current = self._parse_reservations(page)
            entry = next((r for r in current if r["mac"] == mac), None)
            if not entry:
                raise RouterError("Kunci IP untuk perangkat ini tidak ditemukan.")
            result = self._submit(s, "net_dhcp_static_t", page, {
                "IF_ACTION": "delete", "IF_INDEX": str(entry["index"]), "IF_INSTNUM": str(len(current)),
            })
            saved = self._parse_reservations(result) or self._parse_reservations(self._page(s, "net_dhcp_static_t"))
        if any(r["mac"] == mac for r in saved):
            raise RouterError("Router tidak menghapus kunci IP. Coba lagi.")
        return saved

    # Blokir = MAC Filter mode "Discard" (daftar hitam): semua lalu lintas dari MAC itu dibuang router.
    def block(self, mac: str) -> list:
        with self.session() as s:
            page = self._page(s, "sec_macfilter_conf_t")
            current = self._parse_blocklist(page)
            if current["mode"] != "Discard":
                raise RouterError("MAC Filter router sedang mode daftar putih (Permit). Ubah ke Discard dulu di halaman admin router.")
            if any(r["mac"] == mac for r in current["rules"]):
                raise RouterError("Perangkat ini sudah diblokir.")
            result = self._submit(s, "sec_macfilter_conf_t", page, {
                **{k: "NULL" for k in current["basic"]},
                "Enable": "1", "BlackList": "NULL", "Type": "Bridge+Route", "Protocol": "ALL",
                "SrcMacAddr": mac, "DstMacAddr": "00:00:00:00:00:00",
                "MacFilterTarget": "Discard", "MacFilterEnable": "1",
                "IF_ACTION": "new", "IF_INDEX": "-1", "IF_INSTNUM": str(len(current["rules"])),
            })
            saved = self._parse_blocklist(result)
            if not saved["rules"]:
                saved = self._parse_blocklist(self._page(s, "sec_macfilter_conf_t"))
        if not any(r["mac"] == mac for r in saved["rules"]):
            raise RouterError("Router tidak menyimpan blokir. Coba lagi.")
        return [r["mac"] for r in saved["rules"]]

    def unblock(self, mac: str) -> list:
        with self.session() as s:
            page = self._page(s, "sec_macfilter_conf_t")
            current = self._parse_blocklist(page)
            entry = next((r for r in current["rules"] if r["mac"] == mac), None)
            if not entry:
                raise RouterError("Perangkat ini tidak sedang diblokir.")
            result = self._submit(s, "sec_macfilter_conf_t", page, {
                **current["basic"],
                "IF_ACTION": "delete", "IF_INDEX": str(entry["index"]), "IF_INSTNUM": str(len(current["rules"])),
            })
            saved = self._parse_blocklist(result)
            if len(saved["rules"]) >= len(current["rules"]):
                saved = self._parse_blocklist(self._page(s, "sec_macfilter_conf_t"))
        if any(r["mac"] == mac for r in saved["rules"]):
            raise RouterError("Router tidak membuka blokir. Coba lagi.")
        return [r["mac"] for r in saved["rules"]]

    def reboot(self):
        with self.session() as s:
            page = self._page(s, "manager_dev_conf_t")
            try:
                self._submit(s, "manager_dev_conf_t", page, {"IF_ACTION": "devrestart", "flag": "1"})
            except requests.RequestException:
                pass  # router langsung restart dan memutus koneksi


class RouterManager:
    """Tab Router di Liliya: perangkat terhubung, kunci IP (DHCP static), status internet, restart."""

    CACHE_SECONDS = 20

    def __init__(self, config: dict, base_dir: str):
        cfg = config.get("router", {}) or {}
        self.enabled = bool(cfg.get("enabled") and cfg.get("host") and cfg.get("username"))
        self.router = ZteRouter(cfg.get("host", "192.168.1.1"), cfg.get("username", ""), cfg.get("password", "")) if self.enabled else None
        self.names_path = os.path.join(base_dir, "data", "router_devices.json")
        self._cache = None
        self._busy_until = 0
        self._last_ip, self._last_hostname = {}, {}
        self._hostname = socket.gethostname()
        self._own_ip = self._detect_own_ip(cfg.get("host", "192.168.1.1"))
        if self.enabled:
            print(f"[Router] 📶 Manajemen router {cfg.get('host')} aktif")

    @staticmethod
    def _detect_own_ip(router_host: str) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((router_host, 80))
                return sock.getsockname()[0]
        except OSError:
            return ""

    def register_routes(self, app: web.Application):
        app.router.add_get("/api/router/status", self.handle_status)
        app.router.add_post("/api/router/reservations", self.handle_add_reservation)
        app.router.add_delete("/api/router/reservations/{mac}", self.handle_delete_reservation)
        app.router.add_put("/api/router/devices/{mac}", self.handle_rename)
        app.router.add_post("/api/router/block/{mac}", self.handle_block)
        app.router.add_delete("/api/router/block/{mac}", self.handle_unblock)
        app.router.add_post("/api/router/reboot", self.handle_reboot)

    # ---------- Nama & ikon perangkat (disimpan lokal, router tidak menyimpan nama) ----------
    def _load_names(self) -> dict:
        try:
            with open(self.names_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_names(self, names: dict):
        os.makedirs(os.path.dirname(self.names_path), exist_ok=True)
        tmp = self.names_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(names, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.names_path)

    def _decorate(self, snap: dict) -> dict:
        names = self._load_names()
        reserved = {r["mac"]: r["ip"] for r in snap["reservations"]}
        blocked = set(snap.get("blocked", []))
        online = set()
        for c in snap["clients"]:
            online.add(c["mac"])
            meta = names.get(c["mac"], {})
            c["name"] = meta.get("name", "")
            c["icon"] = meta.get("icon", "")
            c["reserved_ip"] = reserved.get(c["mac"])
            c["blocked"] = c["mac"] in blocked
        for r in snap["reservations"]:
            meta = names.get(r["mac"], {})
            r["name"] = meta.get("name", "")
            r["icon"] = meta.get("icon", "")
            r["online"] = r["mac"] in online
            r["blocked"] = r["mac"] in blocked
        # Perangkat diblokir biasanya hilang dari daftar DHCP, jadi tampilkan terpisah agar bisa dibuka lagi
        snap["blocked_devices"] = [{"mac": m, "ip": reserved.get(m) or self._last_ip.get(m, ""),
                                    "hostname": self._last_hostname.get(m, ""), "online": m in online,
                                    "name": names.get(m, {}).get("name", ""), "icon": names.get(m, {}).get("icon", "")}
                                   for m in snap.get("blocked", [])]
        return snap

    def _protected_macs(self, request) -> set:
        """MAC yang tidak boleh diblokir: STB ini sendiri dan perangkat yang sedang membuka Liliya."""
        protected = set()
        for c in (self._cache or {}).get("clients", []):
            if c.get("hostname") and c["hostname"] == self._hostname:
                protected.add(c["mac"])
            if c.get("ip") in (request.remote, self._own_ip):
                protected.add(c["mac"])
        return protected

    @staticmethod
    def _mac(request) -> str:
        mac = request.match_info.get("mac", "").upper().replace("-", ":")
        if not MAC_RE.match(mac):
            raise web.HTTPBadRequest(text="MAC tidak valid")
        return mac

    async def _run(self, fn, *args):
        if not self.enabled:
            raise RouterError("Router belum dikonfigurasi di config.yaml (bagian router).")
        if time.time() < self._busy_until:
            raise RouterError("Router sedang restart, tunggu sekitar 2 menit.")
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    @staticmethod
    def _error(e: Exception, status=502):
        return web.json_response({"error": str(e)}, status=status)

    async def handle_status(self, request):
        if not self.enabled:
            return web.json_response({"enabled": False})
        fresh = request.query.get("refresh") == "1"
        if fresh or not self._cache or time.time() - self._cache["fetched_at"] > self.CACHE_SECONDS:
            try:
                self._cache = await self._run(self.router.snapshot)
            except RouterError as e:
                return self._error(e)
            for c in self._cache["clients"]:
                self._last_ip[c["mac"]] = c["ip"]
                if c["hostname"]:
                    self._last_hostname[c["mac"]] = c["hostname"]
        return web.json_response({"enabled": True, **self._decorate(json.loads(json.dumps(self._cache)))})

    async def handle_add_reservation(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        mac = str(body.get("mac", "")).upper().replace("-", ":")
        ip = str(body.get("ip", "")).strip()
        if not MAC_RE.match(mac) or not IP_RE.match(ip) or not ip.startswith("192.168.1.") or ip.endswith((".0", ".1", ".255")):
            return self._error("MAC atau IP tidak valid (harus 192.168.1.2 – 192.168.1.254).", 400)
        try:
            await self._run(self.router.add_reservation, mac, ip)
        except RouterError as e:
            return self._error(e)
        self._cache = None
        return web.json_response({"status": "ok"})

    async def handle_delete_reservation(self, request):
        mac = self._mac(request)
        try:
            await self._run(self.router.delete_reservation, mac)
        except RouterError as e:
            return self._error(e)
        self._cache = None
        return web.json_response({"status": "ok"})

    async def handle_rename(self, request):
        mac = self._mac(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = str(body.get("name", "")).strip()[:40]
        icon = str(body.get("icon", "")).strip()
        names = self._load_names()
        entry = {k: v for k, v in (("name", name), ("icon", icon if icon in DEVICE_ICONS else "")) if v}
        if entry:
            names[mac] = entry
        else:
            names.pop(mac, None)
        self._save_names(names)
        return web.json_response({"status": "ok"})

    async def handle_block(self, request):
        mac = self._mac(request)
        if not self._cache:
            await self.handle_status(request)
        if mac in self._protected_macs(request):
            return self._error("Perangkat ini tidak bisa diblokir (STB Liliya atau perangkat yang sedang Anda pakai).", 400)
        try:
            await self._run(self.router.block, mac)
        except RouterError as e:
            return self._error(e)
        self._cache = None
        return web.json_response({"status": "ok"})

    async def handle_unblock(self, request):
        mac = self._mac(request)
        try:
            await self._run(self.router.unblock, mac)
        except RouterError as e:
            return self._error(e)
        self._cache = None
        return web.json_response({"status": "ok"})

    async def handle_reboot(self, request):
        try:
            await self._run(self.router.reboot)
        except RouterError as e:
            return self._error(e)
        self._busy_until = time.time() + 120
        self._cache = None
        return web.json_response({"status": "ok"})
