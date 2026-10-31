#!/usr/bin/env python3
"""
=====================================================================
STB Smart Voice Assistant & Smart Display (Gemini AI)
Asisten: BUDI
With Acoustic Isolation (Anti-False-Trigger saat Berita/Musik Diputar)
Powered by Google Gemini Flash, Armbian Linux & WebUI Dashboard
=====================================================================
"""

import os
import sys
import time
import yaml
import threading

from core.audio import AudioEngine
from core.wakeword import WakeWordDetector
from core.tts import TTSEngine
from core.stt import WhisperSTTEngine
from core.gemini_brain import GeminiBrain
from core.web_server import WebServer
from core.smart_home_db import SmartHomeRealtimeDB
from core.firebase_bridge import FirebaseRealtimeBridge
from core.tuya_bridge import TuyaCloudBridge

from skills.media_player import MediaPlayerSkill
from skills.weather import WeatherSkill
from skills.timer_alarm import TimerAlarmSkill
from skills.system_control import SystemControlSkill
from skills.smart_home import SmartHomeSkill
from skills.prayer_times import PrayerTimesSkill
from skills.door_lock import DoorLockSkill

# Filler "sebentar" hanya diputar bila jawaban belum siap dalam waktu ini (perintah cepat tanpa filler)
FILLER_DELAY_S = 0.8

class EventBridge:
    def __init__(self, gemini_brain, tts_engine, media_player, sys_ctrl, audio_engine, smart_home_db=None, web_server=None, stt_engine=None):
        self.gemini = gemini_brain
        self.tts = tts_engine
        self.stt = stt_engine
        self.media_player = media_player
        self.sys_ctrl = sys_ctrl
        self.audio = audio_engine
        self.smart_home_db = smart_home_db
        self.web_server = web_server
        # Perintah dari beberapa HP/browser diproses bergantian agar sesi Gemini & speaker tidak bertabrakan
        self._command_lock = threading.Lock()

    def set_web_server(self, web_server):
        self.web_server = web_server

    def broadcast_state(self, state: str):
        if self.web_server:
            self.web_server.broadcast("state_change", {"state": state})

    def broadcast_user_command(self, text: str):
        if self.web_server:
            self.web_server.broadcast("user_voice_command", {"text": text})

    def broadcast_assistant_reply(self, text: str):
        if self.web_server:
            self.web_server.broadcast("assistant_response", {"text": text})

    def broadcast_now_playing(self, title: str):
        if self.web_server:
            self.web_server.broadcast("now_playing", {"title": title})

    def broadcast_event(self, data: dict):
        if self.web_server:
            msg_type = data.get("type", "generic_event")
            self.web_server.broadcast(msg_type, data)

    def on_web_text_command(self, text: str):
        def _exec():
            with self._command_lock:
                started = time.time()
                try:
                    _run()
                    print(f"[WebCommand] ✅ Selesai dalam {time.time() - started:.1f} dtk: \"{text}\"")
                except Exception as e:
                    print(f"[WebCommand Error] \"{text}\": {e}")
                    self.broadcast_assistant_reply("Maaf, terjadi kesalahan saat memproses perintah. Coba lagi.")
                    self.broadcast_state("idle")

        def _run():
            print(f"[WebCommand] 💬 Perintah: \"{text}\"")
            was_playing = self.media_player.is_playing()
            if was_playing:
                self.media_player.pause_music()
            
            self.broadcast_user_command(text)
            self.broadcast_state("thinking")
            self.broadcast_assistant_reply("Sedang diproses...")
            # Suara jeda hanya bila jawaban belum siap dalam FILLER_DELAY_S (perintah cepat langsung dijawab)
            done = threading.Event()
            filler = threading.Thread(target=self.tts.play_filler, args=(done, FILLER_DELAY_S), daemon=True)
            filler.start()
            
            user_txt, response = self.gemini.process_text_command(text)
            done.set()
            filler.join()
            self.broadcast_assistant_reply(response)
            self.broadcast_state("speaking")
            self.tts.speak(response)
            time.sleep(0.3)
            self.audio.flush()
            
            if getattr(self.media_player, "is_paused", False) and not self.media_player.is_playing():
                self.media_player.resume_music()
                self.broadcast_now_playing(self.media_player.current_title)
            elif self.media_player.is_playing():
                self.broadcast_now_playing(self.media_player.current_title)
                
            self.broadcast_state("idle")
                
        threading.Thread(target=_exec, daemon=True).start()

    def on_web_audio_command(self, audio_base64: str):
        def _exec():
            try:
                import base64
                raw_audio = base64.b64decode(audio_base64)
                self.broadcast_state("thinking")
                self.broadcast_assistant_reply("Mendengarkan ucapan...")
                
                # Transkripsikan audio rekaman browser dengan Whisper STT
                text = self.stt.transcribe(raw_audio)
                if not text:
                    text = "Cek status perangkat"
                    
                print(f"[WebVoice] 🗣️ ANDA (Browser Mic): \"{text}\"")
                self.on_web_text_command(text)
            except Exception as e:
                print(f"[WebVoice Error] {e}")
                self.broadcast_state("idle")
                
        threading.Thread(target=_exec, daemon=True).start()

    def on_web_media_action(self, action: str):
        if action == "stop":
            self.media_player.stop_music()
            self.broadcast_now_playing("")

    def on_web_set_volume(self, volume: int):
        self.sys_ctrl.set_volume(volume)

def load_config(config_path="config.yaml"):
    if not os.path.exists(config_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "config.yaml")
        
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

def main():
    print("=" * 65)
    print("🤖 STB Smart Voice Assistant (Asisten: BUDI)")
    print("Mode: Strict Wake Word + Anti-Background-Echo Isolation")
    print("=" * 65)
    
    config = load_config()
    
    # 0. Mulai Web Server seketika (< 0.1 detik) agar Cloudflare & Web UI langsung 200 OK
    smart_home_db = SmartHomeRealtimeDB()
    web_server = WebServer(config, smart_home_db=smart_home_db, host="0.0.0.0", port=8080)
    web_server.start_background()
    smart_home_db.set_web_server(web_server)

    # 1. Buka seluruh switch volume DAC hardware STB (S905X & USB)
    from core.audio_hw import auto_unmute_hardware_audio
    auto_unmute_hardware_audio()

    # 2. Inisialisasi Audio, TTS & STT Bahasa Indonesia
    audio = AudioEngine(config)
    tts = TTSEngine(config)
    stt = WhisperSTTEngine(model_size="tiny", compute_type="int8")

    # 🔔 Tes speaker via beep PyAudio langsung (sebelum TTS & MPV)
    print("[Main] 🔔 Tes speaker via beep PyAudio...")
    tts.play_startup_beep()
    
    # 3. Inisialisasi Smart Home Realtime Database, Firebase Bridge & Tuya Cloud Bridge
    firebase_bridge = FirebaseRealtimeBridge(config, smart_home_db)
    smart_home_db.set_firebase_bridge(firebase_bridge)
    tuya_bridge = TuyaCloudBridge(config)
    smart_home_db.set_tuya_bridge(tuya_bridge)
    smart_home_skill = SmartHomeSkill(smart_home_db)
    
    # 4. Inisialisasi Skills Lainnya
    media_player = MediaPlayerSkill(config)
    weather = WeatherSkill(config)
    timer = TimerAlarmSkill(tts_engine=tts)
    system_ctrl = SystemControlSkill(config)
    prayer_times = PrayerTimesSkill(config, tts_engine=tts, media_player=media_player)
    
    skills = {
        "smart_home": smart_home_skill,
        "media_player": media_player,
        "weather": weather,
        "timer": timer,
        "system": system_ctrl,
        "prayer_times": prayer_times,
        "door_lock": DoorLockSkill(web_server.door_lock)
    }
    
    # 5. Inisialisasi Wake Word & Gemini AI
    wakeword_cfg = config.get("wakeword", {})
    wakeword_enabled = wakeword_cfg.get("enabled", False)
    
    wakeword = None
    if wakeword_enabled:
        wakeword = WakeWordDetector(config)
    else:
        print("[Main] ⚡ Auto-Dengar (Wake Word) DINONAKTIFKAN.")
        print("      Mode: Push & Speak aktif (Hemat Daya & 0% CPU Idle).")

    gemini = GeminiBrain(config, skills)
    
    # 6. Hubungkan Web Server & Event Bridge
    bridge = EventBridge(gemini, tts, media_player, system_ctrl, audio, smart_home_db, web_server=web_server, stt_engine=stt)
    web_server.event_bridge = bridge
    media_player.set_event_bridge(bridge)
    prayer_times.set_event_bridge(bridge)
    bridge.set_web_server(web_server)
    gemini.set_web_server(web_server)
    
    # 6. Sapaan Awal BUDI
    print("[Main] 🔊 Memutar sapaan awal BUDI...")
    bridge.broadcast_state("speaking")
    bridge.broadcast_assistant_reply("Budi siap digunakan.")
    audio.play_beep(frequency=523, duration=0.08)
    time.sleep(0.04)
    audio.play_beep(frequency=659, duration=0.1)
    tts.speak("Budi siap digunakan.")
    time.sleep(0.3)
    audio.flush()
    bridge.broadcast_state("idle")
    
    try:
        if wakeword_enabled and wakeword:
            print("\n[Main] 👂 Standby mendengarkan kata pemicu ('Budi' / 'Halo Budi')...\n")
            while True:
                is_playing = media_player.is_playing()
                
                # Stage 0: Baca audio chunk dan periksa level energi suara (RMS)
                raw_bytes, audio_array, rms = audio.read_chunk()
                
                # Debug RMS setiap 50 frame (~3 detik)
                if not hasattr(main, '_dbg_count'):
                    main._dbg_count = 0
                main._dbg_count += 1
                if main._dbg_count % 50 == 0:
                    print(f"[AudioDebug] 🎙️ RMS Level: {rms:.1f}", flush=True)

                # Jika musik/berita sedang berputar, terapkan noise gate
                if is_playing and rms < 80:
                    time.sleep(0.01)
                    continue
                    
                # Stage 1: Wake word terdeteksi ("Budi" / "Halo Budi") secara instan
                if wakeword.process(audio_array, is_media_playing=is_playing):
                    # 1. Jeda musik/berita seketika agar hening saat merekam suara Anda
                    was_playing = media_player.is_playing()
                    if was_playing:
                        media_player.pause_music()
                    
                    # Buang audio sisa berita di buffer
                    audio.flush()
                    time.sleep(0.05)
                    audio.flush()
                    
                    # 2. Feedback Beep instan
                    audio.play_beep(frequency=880, duration=0.08)
                    
                    # 3. Merekam suara perintah pengguna
                    bridge.broadcast_state("listening")
                    bridge.broadcast_user_command("Mendengarkan ucapan Anda...")
                    
                    wav_bytes = audio.record_voice_command(initial_wait_timeout=3.5)
                    
                    # 4. Transkripsi Bahasa Indonesia secara instan via Whisper
                    user_text = ""
                    if wav_bytes and len(wav_bytes) >= 4000:
                        user_text = stt.transcribe(wav_bytes)
                    
                    if user_text:
                        print(f"\n[STTEngine] 🗣️ ANDA: \"{user_text}\"")
                        # Tampilkan teks ucapan Anda di Web Dashboard seketika!
                        bridge.broadcast_user_command(user_text)
                        bridge.broadcast_state("thinking")
                        bridge.broadcast_assistant_reply("Sedang diproses...")
                        
                        done = threading.Event()
                        filler = threading.Thread(target=tts.play_filler, args=(done, FILLER_DELAY_S), daemon=True)
                        filler.start()
                        
                        # Kirim teks bersih ke Gemini
                        user_txt, response_text = gemini.process_text_command(user_text)
                        done.set()
                        filler.join()
                        
                        # Tampilkan balasan BUDI di layar dan bacakan
                        bridge.broadcast_assistant_reply(response_text)
                        bridge.broadcast_state("speaking")
                        tts.speak(response_text)
                        
                        # 5. Lanjutkan berita/musik dari detik terakhir jika tadi dijeda
                        if media_player.is_paused:
                            media_player.resume_music()
                            bridge.broadcast_now_playing(media_player.current_title)
                        elif media_player.is_playing():
                            bridge.broadcast_now_playing(media_player.current_title)
                            
                    else:
                        print("\n[Main] ⚠️ Tidak ada ucapan terdeteksi / hening.")
                        if media_player.is_paused:
                            media_player.resume_music()
                            bridge.broadcast_now_playing(media_player.current_title)
                    
                    # 6. KEMBALI STANDBY
                    wakeword.reset()
                    time.sleep(0.3)
                    audio.flush()
                    bridge.broadcast_state("idle")
                    print("\n[Main] 👂 Selesai. Kembali standby (menunggu panggilan 'Budi')...")
        else:
            print("\n[Main] 🟢 Service BUDI aktif & standby (Mode Push & Speak via Web UI / Dashboard / API).")
            print("      🚀 CPU Idle: 0% (Auto-dengar mikrofon dinonaktifkan)\n")
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n[Main] Menutup asisten...")
    finally:
        audio.close()
        media_player.stop_music()
        if firebase_bridge:
            firebase_bridge.stop()
        print("[Main] Selesai.")

if __name__ == "__main__":
    main()
