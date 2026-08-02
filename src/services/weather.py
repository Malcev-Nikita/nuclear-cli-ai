"""Погода: Open-Meteo (без ключа, ~0.3 c). Фраза сразу «под озвучку»."""

from __future__ import annotations

from datetime import date, timedelta

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


# Дни недели: основа слова -> номер (0 = понедельник) и как назвать в ответе.
_WEEKDAY_STEMS = [("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3),
                  ("пятниц", 4), ("суббот", 5), ("воскресен", 6)]
_WEEKDAY_SPOKEN = ["в понедельник", "во вторник", "в среду", "в четверг",
                   "в пятницу", "в субботу", "в воскресенье"]
_RAIN_CODES = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))
_SNOW_CODES = set(range(71, 78)) | {85, 86}
# Разговорные названия: геокодер по «питере» находит деревню Питер в Пермском крае.
_CITY_ALIASES = {"питер": "Санкт-Петербург", "спб": "Санкт-Петербург", "мск": "Москва",
                 "ебург": "Екатеринбург", "нижний": "Нижний Новгород", "новосиб": "Новосибирск"}


def _dealias(city: str) -> str:
    """«питере» -> «Санкт-Петербург»; «нижний тагил» не трогаем (слишком длинный хвост)."""
    low = city.strip().lower()
    for short, full in _CITY_ALIASES.items():
        if low == short or (low.startswith(short) and len(low) - len(short) <= 2):
            return full
    return city


def parse_when(text: str, today: date | None = None) -> tuple[list[int], str] | None:
    """«завтра»/«на выходных»/«в пятницу» -> (смещения в днях, как назвать)."""
    text = (text or "").strip().lower()
    today = today or date.today()
    if not text or "сегодня" in text:
        return ([0], "сегодня") if "сегодня" in text else None
    if "послезавтра" in text:
        return [2], "послезавтра"
    if "завтра" in text:
        return [1], "завтра"
    if "выходн" in text:
        # ближайшие суббота и воскресенье (сегодняшние выходные тоже считаем)
        saturday = (5 - today.weekday()) % 7
        return [saturday, saturday + 1], "на выходных"
    for stem, number in _WEEKDAY_STEMS:
        if stem in text:
            offset = (number - today.weekday()) % 7 or 7  # «в среду» в среду = через неделю
            return [offset], _WEEKDAY_SPOKEN[number]
    return None


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

    # -- прогноз на будущие дни

    def _daily(self, city: str) -> tuple[str, list[dict]]:
        city = (city or self.default_city).strip()
        lat, lon, name = self._geocode(city) if city else self._locate_by_ip()
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                          "precipitation_probability_max"),
                "timezone": "auto", "forecast_days": 9,
            },
            timeout=10,
        )
        resp.raise_for_status()
        daily = resp.json()["daily"]
        days = [
            {
                "code": daily["weather_code"][i],
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "rain": daily.get("precipitation_probability_max", [None] * 9)[i],
            }
            for i in range(len(daily["time"]))
        ]
        return name, days

    def forecast(self, city: str = "", when: str = "завтра", today: date | None = None) -> str:
        today = today or date.today()
        offsets, label = parse_when(when, today) or ([1], "завтра")
        try:
            name, days = self._daily(city)
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            return f"Не смог узнать прогноз: {error}"
        parts = []
        for offset in offsets:
            if offset >= len(days):
                continue
            day = days[offset]
            piece = f"{_WMO_DESC.get(day['code'], 'без осадков')}, от " \
                    f"{spoken_temp(day['low'], genitive=True)} до " \
                    f"{spoken_temp(day['high'], genitive=True)}"
            if day["rain"] and day["rain"] >= 30:
                piece += f", осадки с вероятностью {int(day['rain'])} процентов"
            # для выходных подписываем каждый день, для одного — не надо
            parts.append(f"{_WEEKDAY_SPOKEN[(today + timedelta(days=offset)).weekday()]} {piece}"
                         if len(offsets) > 1 else piece)
        if not parts:
            return "Так далеко прогноза нет"
        where = f" в городе {name}" if name else ""
        return f"{label.capitalize()}{where} {'; '.join(parts)}"

    def will_precipitate(self, city: str = "", when: str = "завтра", kind: str = "дождь",
                         today: date | None = None) -> str:
        offsets, label = parse_when(when, today or date.today()) or ([1], "завтра")
        codes = _SNOW_CODES if kind.startswith("снег") else _RAIN_CODES
        try:
            _, days = self._daily(city)
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            return f"Не смог узнать прогноз: {error}"
        hits = [days[o] for o in offsets if o < len(days) and days[o]["code"] in codes]
        if not hits:
            return f"{label.capitalize()} {kind} не обещают"
        chance = max((h["rain"] or 0) for h in hits)
        answer = f"{label.capitalize()} обещают {kind}"
        return answer + (f", вероятность {int(chance)} процентов" if chance else "")

    def _geocode(self, city: str) -> tuple[float, float, str]:
        """Город -> координаты; падежи («в казани») лечатся перебором окончаний.

        Из кандидатов берём самый населённый: «казани» не находится вовсе,
        «казана» находит село Казанак — и погода была бы оттуда.
        """
        if city in self._geo_cache:
            return self._geo_cache[city]
        wanted = _dealias(city)
        attempts = [wanted]
        if wanted[-1:] in "еиую":
            stem = wanted[:-1]
            attempts += [stem + "ь", stem + "а", stem]
        fallback = None
        for attempt in attempts:
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": attempt, "count": 5, "language": "ru"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            if not results:
                continue
            best = max(results, key=lambda r: r.get("population") or 0)
            if (best.get("population") or 0) >= 10000:  # настоящий город — берём сразу
                return self._remember(city, best)
            fallback = fallback or results[0]  # только сёла — вернёмся, если лучше не будет
        if fallback:
            return self._remember(city, fallback)
        raise ValueError(f"город «{city}» не нашёлся")

    def _remember(self, city: str, place: dict) -> tuple[float, float, str]:
        found = (place["latitude"], place["longitude"], place["name"])
        self._geo_cache[city] = found
        return found

    @staticmethod
    def _locate_by_ip() -> tuple[float, float, str]:
        resp = requests.get("http://ip-api.com/json/?fields=lat,lon,city&lang=ru", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["lat"], data["lon"], data.get("city", "")
