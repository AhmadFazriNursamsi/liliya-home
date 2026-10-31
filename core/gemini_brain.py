import os
import re
from typing import Optional
from google import genai
from google.genai import types

STABLE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash"
]

class GeminiBrain:
    def __init__(self, config, skills, web_server=None):
        self.config = config
        self.gemini_config = config.get("gemini", {})
        self.api_keys = self.gemini_config.get("api_keys", [])
        self.model_name = self.gemini_config.get("model_name", "gemini-2.5-flash")
        self.fallback_models = self.gemini_config.get("fallback_models", STABLE_MODELS)
        self.system_prompt = self.gemini_config.get("system_prompt", "Kamu adalah asisten suara pintar bernama Budi.")
        self.web_server = web_server
        # "Thinking" model Gemini membuat jawaban 15-20 detik; untuk perintah rumah cukup 0 (±1 detik).
        # Isi angka di config (gemini.thinking_budget) bila butuh penalaran lebih, atau null untuk default model.
        self.thinking_budget = self.gemini_config.get("thinking_budget", 0)
        self._no_thinking_config = set()  # model yang menolak pengaturan thinking
        
        # Usage metrics tracker
        self.total_tokens_used = 0
        self.total_requests = 0
        self.last_prompt_tokens = 0
        self.last_candidate_tokens = 0
        
        # Skills registry
        self.media_player = skills.get("media_player")
        self.weather = skills.get("weather")
        self.timer = skills.get("timer")
        self.system_ctrl = skills.get("system")
        self.smart_home = skills.get("smart_home")
        self.prayer_times = skills.get("prayer_times")
        self.door_lock = skills.get("door_lock")
        
        # Validasi API Keys
        self.valid_keys = [k for k in self.api_keys if k and "MASUKKAN_GEMINI" not in k]
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key and env_key not in self.valid_keys:
            self.valid_keys.append(env_key)
            
        self.key_index = 0
        self.current_key = None
        self.client = None
        self.chat = None
        self.active_model = self.model_name
        self._init_gemini_client()

    def set_web_server(self, web_server):
        self.web_server = web_server
        self._broadcast_usage()

    def _broadcast_usage(self):
        if self.web_server:
            self.web_server.broadcast("usage_stat", {
                "total_tokens": self.total_tokens_used,
                "total_requests": self.total_requests,
                "last_prompt_tokens": self.last_prompt_tokens,
                "last_candidate_tokens": self.last_candidate_tokens,
                "active_model": self.active_model,
                "active_key": f"...{self.current_key[-6:]}" if self.current_key else "None",
                "key_count": len(self.valid_keys)
            })

    def _track_usage(self, response):
        try:
            self.total_requests += 1
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                meta = response.usage_metadata
                p_tok = getattr(meta, "prompt_token_count", 0) or 0
                c_tok = getattr(meta, "candidates_token_count", 0) or 0
                t_tok = getattr(meta, "total_token_count", 0) or (p_tok + c_tok)
                
                self.last_prompt_tokens = p_tok
                self.last_candidate_tokens = c_tok
                self.total_tokens_used += t_tok
            else:
                self.total_tokens_used += 150
        except Exception:
            pass
            
        self._broadcast_usage()

    def _rotate_api_key(self):
        if not self.valid_keys:
            return False
            
        self.key_index = (self.key_index + 1) % len(self.valid_keys)
        self.current_key = self.valid_keys[self.key_index]
        self.client = genai.Client(api_key=self.current_key)
        try:
            self.chat = self._create_chat_session(self.active_model)
        except Exception:
            pass
            
        print(f"[GeminiBrain] 🔄 Beralih ke API Key [{self.key_index + 1}/{len(self.valid_keys)}]: ...{self.current_key[-6:]}")
        self._broadcast_usage()
        return True

    def _build_tools(self):
        tools = []
        if self.smart_home:
            tools.extend([
                self.smart_home.control_smart_home_device,
                self.smart_home.get_smart_home_status
            ])
        if self.media_player:
            tools.extend([
                self.media_player.play_youtube,
                self.media_player.stop_music,
                self.media_player.play_radio
            ])
        if self.weather:
            tools.append(self.weather.get_weather)
        if self.timer:
            tools.extend([
                self.timer.set_timer,
                self.timer.cancel_timer
            ])
        if self.system_ctrl:
            tools.extend([
                self.system_ctrl.get_time,
                self.system_ctrl.get_volume,
                self.system_ctrl.set_volume,
                self.system_ctrl.get_system_status
            ])
        if self.prayer_times:
            tools.extend([
                self.prayer_times.get_prayer_times,
                self.prayer_times.get_next_prayer
            ])
        if self.door_lock:
            tools.extend([
                self.door_lock.lock_door,
                self.door_lock.unlock_door,
                self.door_lock.get_door_status
            ])
        return tools

    def _create_chat_session(self, model_name: str):
        tools = self._build_tools()
        extra = {}
        if self.thinking_budget is not None and model_name not in self._no_thinking_config:
            extra["thinking_config"] = types.ThinkingConfig(thinking_budget=int(self.thinking_budget))
        return self.client.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=tools,
                temperature=0.3,
                max_output_tokens=250,
                **extra
            )
        )

    def _init_gemini_client(self):
        if not self.valid_keys:
            print("[GeminiBrain Warning] ⚠️ Belum ada Gemini API Key yang disetel di config.yaml!")
            return

        self.current_key = self.valid_keys[self.key_index]
        self.client = genai.Client(api_key=self.current_key)
        print(f"[GeminiBrain] 🔑 Menggunakan API Key [{self.key_index + 1}/{len(self.valid_keys)}]: ...{self.current_key[-6:]}")

        models_to_check = [self.model_name] + [m for m in STABLE_MODELS if m != self.model_name]
        for mdl in models_to_check:
            try:
                self.chat = self._create_chat_session(mdl)
                self.active_model = mdl
                print(f"[GeminiBrain] 🚀 Gemini AI ({mdl}) siap dengan {len(self._build_tools())} tools.")
                self._broadcast_usage()
                return
            except Exception:
                continue

    def _sanitize_output(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        text = raw_text.strip()
        text = re.sub(r'\[USER\]:.*?(?=\[ASISTEN\]|$)', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\[ASISTEN\]:\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[.*?\]', '', text)
        text = text.replace('**', '').replace('*', '').replace('`', '').strip()
        return text

    def _try_fast_intent(self, text: str) -> Optional[str]:
        if not text:
            return None
        t = text.lower().strip()
        t_clean = re.sub(r'^(halo budi|hey budi|hai budi|budi)[,\s]+', '', t).strip()
        
        # 1. Hentikan musik / radio / audio
        if t_clean in ["stop", "stop musik", "berhenti", "matikan musik", "hentikan musik", "stop lagu", "matikan lagu", "hentikan lagu", "diam", "matikan radio", "stop radio"]:
            if self.media_player:
                return self.media_player.stop_music()

        # 2. Perintah mematikan lampu / smart home (contoh: "matikan lampu", "matikan semua lampu", "padamkan lampu ruang tamu")
        m_turn_off = re.match(r'^(matikan|padamkan|tutup|off)\s+(lampu\s+[\w\s]+|semua\s+lampu|lampu|lampunya|ac|ac\s+kamar|kipas|kipas\s+angin|semua\s+perangkat)$', t_clean)
        if m_turn_off and self.smart_home:
            dev_target = m_turn_off.group(2).strip()
            return self.smart_home.control_smart_home_device(dev_target, "turn_off")
            
        # 3. Perintah menyalakan lampu / smart home (contoh: "nyalakan lampu", "hidupkan lampu ruang tamu")
        m_turn_on = re.match(r'^(nyalakan|hidupkan|buka|on)\s+(lampu\s+[\w\s]+|semua\s+lampu|lampu|lampunya|ac|ac\s+kamar|kipas|kipas\s+angin|semua\s+perangkat)$', t_clean)
        if m_turn_on and self.smart_home:
            dev_target = m_turn_on.group(2).strip()
            return self.smart_home.control_smart_home_device(dev_target, "turn_on")

        # Kunci pintu (membuka kunci lewat suara sengaja ditolak, lihat DoorLockSkill)
        if self.door_lock:
            if re.match(r'^(kunci|kuncikan|tolong kunci)\s+(pintu|pintunya|pintu depan)$', t_clean):
                return self.door_lock.lock_door()
            if re.match(r'^(buka kunci|buka)\s+(pintu|pintunya|pintu depan)$', t_clean):
                return self.door_lock.unlock_door()
            if re.match(r'^(status pintu|cek pintu|pintu sudah dikunci\??|apakah pintu sudah dikunci\??|pintu terkunci\??)$', t_clean):
                return self.door_lock.get_door_status()

        # 4. Permintaan memutar adzan manual (dibatasi hanya saat waktu sholat tiba secara otomatis)
        if any(w in t_clean for w in ["putar adzan", "bunyikan adzan", "kumandangkan adzan", "mainkan adzan", "nyalakan adzan", "setel adzan", "adzan"]):
            if "jadwal" not in t_clean and "waktu" not in t_clean:
                return "Adzan akan berkumandang secara otomatis tepat saat waktu sholat tiba."

        return None

    def process_text_command(self, text: str) -> tuple[str, str]:
        # 1. Cek fast-path intent lokal (eksekusi instan 0ms tanpa halusinasi AI)
        fast_ans = self._try_fast_intent(text)
        if fast_ans:
            print(f"[GeminiBrain FastIntent] ⚡ Eksekusi instan: \"{text}\" -> {fast_ans}")
            return text, fast_ans

        if not self.client:
            self._init_gemini_client()
            if not self.client:
                return text, "API Key belum disetel."

        models_to_try = [self.active_model] + [m for m in STABLE_MODELS if m != self.active_model]

        for mdl in models_to_try:
            for _ in range(len(self.valid_keys)):
                try:
                    if not self.chat or self.active_model != mdl:
                        self.chat = self._create_chat_session(mdl)
                        self.active_model = mdl
                    else:
                        try:
                            if hasattr(self.chat, "get_history") and len(self.chat.get_history()) > 6:
                                self.chat = self._create_chat_session(mdl)
                        except Exception:
                            pass

                    response = self.chat.send_message(text)
                    self._track_usage(response)
                    clean_ans = self._sanitize_output(response.text)
                    return text, clean_ans
                except Exception as e:
                    err_str = str(e)
                    print(f"[GeminiBrain on {mdl}] {err_str}")

                    if "thinking" in err_str.lower() and mdl not in self._no_thinking_config:
                        # Model ini tidak mendukung pengaturan thinking: ulangi tanpa pengaturan tersebut
                        self._no_thinking_config.add(mdl)
                        self.chat = None
                        continue
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        self._rotate_api_key()
                        continue
                    elif "503" in err_str or "UNAVAILABLE" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
                        break # Coba model berikutnya
                    else:
                        break

        return text, "Perintah telah diproses oleh smart home."
