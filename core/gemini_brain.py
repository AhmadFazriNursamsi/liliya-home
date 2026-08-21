import itertools
import os
import re
import time
from google import genai
from google.genai import types

class GeminiBrain:
    def __init__(self, config, skills: dict, web_server=None):
        self.config = config
        self.gemini_config = config.get("gemini", {})
        self.api_keys = self.gemini_config.get("api_keys", [])
        self.model_name = self.gemini_config.get("model_name", "gemini-3.1-flash-lite")
        self.fallback_models = self.gemini_config.get("fallback_models", ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash"])
        self.system_prompt = self.gemini_config.get("system_prompt", "Kamu adalah asisten suara pintar bernama Budi.")
        self.web_server = web_server
        
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
        return tools

    def _create_chat_session(self, model_name: str):
        tools = self._build_tools()
        return self.client.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=tools,
                temperature=0.3,
                max_output_tokens=250
            )
        )

    def _init_gemini_client(self):
        if not self.valid_keys:
            print("[GeminiBrain Warning] ⚠️ Belum ada Gemini API Key yang disetel di config.yaml!")
            return

        self.current_key = self.valid_keys[self.key_index]
        self.client = genai.Client(api_key=self.current_key)
        print(f"[GeminiBrain] 🔑 Menggunakan API Key [{self.key_index + 1}/{len(self.valid_keys)}]: ...{self.current_key[-6:]}")

        for mdl in [self.model_name] + self.fallback_models:
            try:
                self.chat = self._create_chat_session(mdl)
                self.active_model = mdl
                print(f"[GeminiBrain] 🚀 Gemini AI ({mdl}) siap dengan {len(self._build_tools())} tools.")
                self._broadcast_usage()
                return
            except Exception as e:
                print(f"[GeminiBrain Warning] Model {mdl} belum tersedia: {e}")

    def _sanitize_output(self, raw_text: str) -> str:
        """Membersihkan teks balasan dari tag markdown atau label teknis agar TTS membaca alami."""
        if not raw_text:
            return ""
        text = raw_text.strip()
        # Bersihkan format [USER] / [ASISTEN] jika model mengembalikannya
        text = re.sub(r'\[USER\]:.*?(?=\[ASISTEN\]|$)', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\[ASISTEN\]:\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[.*?\]', '', text) # Hapus tag kurung siku lain
        text = text.replace('**', '').replace('*', '').replace('`', '').strip()
        return text

    def process_audio_command(self, wav_bytes: bytes) -> tuple[str, str]:
        if not self.client:
            self._init_gemini_client()
            if not self.client:
                return "", "API Key Gemini belum disetel."

        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        instruction = (
            "Dengarkan audio suara pengguna ini. "
            "Jika pengguna meminta mengontrol lampu/AC/perangkat smart home, memutar lagu, atau bertanya cuaca/jam, panggil tool yang sesuai. "
            "Berikan balasan suara singkat dan ramah dalam bahasa Indonesia untuk dibacakan langsung melalui speaker. "
            "Jika suara hening/tidak jelas, jawab: 'Maaf, saya belum mendengar perintah dengan jelas.'"
        )
        
        models_to_try = [self.active_model] + [m for m in self.fallback_models if m != self.active_model]

        for mdl in models_to_try:
            for _ in range(len(self.valid_keys)):
                try:
                    if not self.chat or self.active_model != mdl:
                        self.chat = self._create_chat_session(mdl)
                        self.active_model = mdl

                    response = self.chat.send_message([audio_part, instruction])
                    self._track_usage(response)
                    
                    raw_ans = response.text.strip() if response.text else "Perintah telah dijalankan."
                    clean_ans = self._sanitize_output(raw_ans)
                    if not clean_ans:
                        clean_ans = "Perintah telah dijalankan."
                    return "", clean_ans
                    
                except Exception as e:
                    err_str = str(e)
                    print(f"[GeminiBrain Error on {mdl}] {err_str}")
                    
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        if len(self.valid_keys) > 1:
                            print("[GeminiBrain] ⚠️ Kuota key habis, merotasi ke API Key berikutnya...")
                            self._rotate_api_key()
                            continue
                        else:
                            break # Coba model berikutnya
                    elif "503" in err_str or "UNAVAILABLE" in err_str:
                        break # Coba model berikutnya
                    else:
                        break

        return "", "Semua kuota API sedang penuh. Silakan coba kembali dalam 30 detik."

    def process_text_command(self, text: str) -> tuple[str, str]:
        if not self.client:
            self._init_gemini_client()
            if not self.client:
                return text, "API Key belum disetel."

        models_to_try = [self.active_model] + [m for m in self.fallback_models if m != self.active_model]

        for mdl in models_to_try:
            for _ in range(len(self.valid_keys)):
                try:
                    if not self.chat or self.active_model != mdl:
                        self.chat = self._create_chat_session(mdl)
                        self.active_model = mdl

                    response = self.chat.send_message(text)
                    self._track_usage(response)
                    clean_ans = self._sanitize_output(response.text)
                    return text, clean_ans
                except Exception as e:
                    err_str = str(e)
                    print(f"[GeminiBrain Error on {mdl}] {err_str}")
                    
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        if len(self.valid_keys) > 1:
                            print("[GeminiBrain] ⚠️ Kuota key habis, merotasi ke API Key berikutnya...")
                            self._rotate_api_key()
                            continue
                        else:
                            break
                    elif "503" in err_str or "UNAVAILABLE" in err_str:
                        break
                    else:
                        break

        return text, "Batas kuota permintaan API tercapai. Silakan tunggu sesaat."
