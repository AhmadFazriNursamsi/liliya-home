import requests

WEATHER_CODES = {
    0: "Cerah tanpa awan",
    1: "Sebagian besar cerah",
    2: "Sebagian berawan",
    3: "Mendung dan berawan",
    45: "Berkabut",
    48: "Kabut tebal",
    51: "Gerimis ringan",
    53: "Gerimis sedang",
    55: "Gerimis lebat",
    61: "Hujan ringan",
    63: "Hujan sedang",
    65: "Hujan lebat",
    80: "Hujan rintik-rintik",
    81: "Hujan deras",
    95: "Badai petir"
}

class WeatherSkill:
    def __init__(self, config):
        self.default_city = config.get("skills", {}).get("default_city", "Jakarta")

    def get_weather(self, city: str = "") -> str:
        """Mengambil data cuaca terkini dari Open-Meteo API."""
        target_city = city if city and city.strip() else self.default_city
        try:
            # 1. Geocoding kota
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={target_city}&count=1&language=id&format=json"
            geo_resp = requests.get(geo_url, timeout=5).json()
            
            if not geo_resp.get("results"):
                return f"Maaf, informasi cuaca untuk kota {target_city} tidak ditemukan."
                
            loc = geo_resp["results"][0]
            lat = loc["latitude"]
            lon = loc["longitude"]
            city_name = loc.get("name", target_city)
            country = loc.get("country", "")

            # 2. Ambil cuaca realtime
            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&timezone=auto"
            w_resp = requests.get(weather_url, timeout=5).json()
            
            current = w_resp.get("current", {})
            temp = current.get("temperature_2m", "--")
            humidity = current.get("relative_humidity_2m", "--")
            code = current.get("weather_code", 0)
            condition = WEATHER_CODES.get(code, "Cerah berawan")
            
            return f"Cuaca di {city_name} saat ini {condition}, suhu sekitar {temp} derajat Celsius dengan kelembapan {humidity}%."
        except Exception as e:
            return f"Maaf, terjadi kendala saat mengecek cuaca {target_city}."
