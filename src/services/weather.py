"""Погода: Open-Meteo (без ключа, ~0.3 c). Фраза сразу «под озвучку»."""

from __future__ import annotations

import requests

# Расшифровка кодов погоды WMO.
_WMO_DESC = {
    0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "сильная ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень",
    85: "небольшой снегопад", 86: "снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}


def spoken_temp(value, genitive: bool = False) -> str:
    degrees = int(round(float(value)))
    if degrees > 0:
        return f"плюс {degrees}"
    if degrees < 0:
        return f"минус {abs(degrees)}"
    return "нуля" if genitive else "ноль"


class OpenMeteoWeather:
    def __init__(self, default_city: str = ""):
        self.default_city = default_city
        self._geo_cache: dict[str, tuple[float, float, str]] = {}

    def get(self, city: str = "") -> str:
        """Город: из команды -> дефолт из конфига -> по IP."""
        city = (city or self.default_city).strip()
        try:
            lat, lon, name = self._geocode(city) if city else self._locate_by_ip()
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,weather_code",
                    "daily": "temperature_2m_min,temperature_2m_max",
                    "timezone": "auto", "forecast_days": 1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            current = data["current"]
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            return f"Не смог узнать погоду: {error}"

        place = f"В городе {name} сейчас" if name else "Сейчас"
        phrase = f"{place} {spoken_temp(current['temperature_2m'])}"
        desc = _WMO_DESC.get(current.get("weather_code"))
        if desc:
            phrase += f", {desc}"
        phrase += f", ощущается как {spoken_temp(current['apparent_temperature'])}"
        daily = data.get("daily") or {}
        if daily.get("temperature_2m_min") and daily.get("temperature_2m_max"):
            low = spoken_temp(daily["temperature_2m_min"][0], genitive=True)
            high = spoken_temp(daily["temperature_2m_max"][0], genitive=True)
            phrase += f". Сегодня от {low} до {high}"
        return phrase

    def _geocode(self, city: str) -> tuple[float, float, str]:
        """Город -> координаты; падежи («в казани») лечатся перебором окончаний."""
        if city in self._geo_cache:
            return self._geo_cache[city]
        attempts = [city]
        if city[-1:] in "еиую":
            stem = city[:-1]
            attempts += [stem + "а", stem + "ь", stem]
        for attempt in attempts:
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": attempt, "count": 1, "language": "ru"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            if results:
                found = (results[0]["latitude"], results[0]["longitude"], results[0]["name"])
                self._geo_cache[city] = found
                return found
        raise ValueError(f"город «{city}» не нашёлся")

    @staticmethod
    def _locate_by_ip() -> tuple[float, float, str]:
        resp = requests.get("http://ip-api.com/json/?fields=lat,lon,city&lang=ru", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["lat"], data["lon"], data.get("city", "")
