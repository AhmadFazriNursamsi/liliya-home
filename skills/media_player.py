import urllib.parse

RADIO_STATIONS = {
    "prambors": "https://stream.radiojar.com/7csmg90fuqruv",
    "gen fm": "https://stream.radiojar.com/078r4uvfuqruv",
    "jak fm": "https://stream.radiojar.com/84u8v8vfuqruv",
    "hard rock": "https://stream.radiojar.com/v2m6b4vfuqruv",
    "elshinta": "https://stream.elshinta.com/live"
}

class MediaPlayerSkill:
    """
    Pemutar Media Ringan & Cepat:
    Mengirimkan perintah redirect langsung ke YouTube di HP/Laptop pengguna
    tanpa membebani CPU/RAM STB.
    """
    def __init__(self, config, event_bridge=None):
        self.config = config.get("skills", {})
        self.event_bridge = event_bridge
        self.current_title = ""
        self.is_paused = False

    def set_event_bridge(self, event_bridge):
        self.event_bridge = event_bridge

    def pause_music(self):
        pass

    def resume_music(self):
        pass

    def stop_music(self) -> str:
        title = self.current_title
        self.current_title = ""
        if self.event_bridge:
            self.event_bridge.broadcast_now_playing("")
        return f"Musik '{title}' telah dihentikan." if title else "Musik telah dihentikan."

    def is_playing(self) -> bool:
        return bool(self.current_title)

    def play_radio(self, station_name: str) -> str:
        station_key = station_name.lower().strip()
        stream_url = None
        
        for name, url in RADIO_STATIONS.items():
            if name in station_key or station_key in name:
                stream_url = url
                station_name = name.title()
                break
                
        if not stream_url:
            station_name = "Prambors FM"
            stream_url = RADIO_STATIONS["prambors"]

        title_display = f"Radio {station_name}"
        self.current_title = title_display
        
        if self.event_bridge:
            self.event_bridge.broadcast_now_playing(f"📻 {title_display}")
            self.event_bridge.broadcast_event({
                "type": "open_url",
                "url": stream_url,
                "title": title_display
            })

        return f"Baik, membuka streaming radio {station_name} untuk Anda."

    def play_youtube(self, query: str) -> str:
        search_query = query.strip()
        title_display = query.title()
        self.current_title = title_display
        
        encoded_query = urllib.parse.quote_plus(search_query)
        youtube_url = f"https://www.youtube.com/results?search_query={encoded_query}"

        print(f"[MediaPlayer] 🚀 Mengirim link YouTube langsung ke HP/Laptop: {youtube_url}")

        if self.event_bridge:
            self.event_bridge.broadcast_now_playing(f"🎬 YouTube: {title_display}")
            self.event_bridge.broadcast_event({
                "type": "open_url",
                "url": youtube_url,
                "title": title_display
            })

        return f"Baik, membuka lagu {query} di YouTube untuk Anda."
