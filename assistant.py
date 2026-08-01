#!/usr/bin/env python3
"""Nuclear CLI AI — этап 1: текстовый агент для управления Nuclear голосовыми командами.

Цепочка: команда → быстрый regex-роутер → (если не распознано) LLM через Ollama
с узкими инструментами → MCP-сервер Nuclear → плеер.

Запуск:  python assistant.py
Конфиг:  config.py (env-переменные с теми же именами переопределяют).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time

import requests

from config import (
    HTTP_TIMEOUT,
    NUCLEAR_MCP_URL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_URL,
    WEATHER_CITY,
)


# ---------------------------------------------------------------------------
# MCP-клиент (streamable HTTP, handshake проверен против Nuclear 1.43)
# ---------------------------------------------------------------------------

class NuclearError(Exception):
    pass


class McpClient:
    def __init__(self, url: str):
        self.url = url
        self.session_id: str | None = None
        self._id = 0
        self._http = requests.Session()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            # Оба типа обязательны: без text/event-stream сервер отвечает 406.
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, body: dict) -> requests.Response:
        return self._http.post(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            timeout=HTTP_TIMEOUT,
        )

    @staticmethod
    def _parse_rpc(resp: requests.Response) -> dict:
        """Ответ приходит либо голым JSON, либо в SSE-обёртке (data: {...}).

        Декодируем сами: text/event-stream приходит без charset, и requests
        по умолчанию читает его как latin-1 — кириллица превращается в кашу.
        """
        text = resp.content.decode("utf-8", errors="replace")
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "jsonrpc" in obj:
                    return obj
            raise NuclearError(f"В SSE-ответе не нашлось JSON-RPC сообщения: {text[:200]}")
        return json.loads(text)

    def handshake(self) -> None:
        self.session_id = None
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nuclear-cli-ai", "version": "0.1"},
            },
        })
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            raise NuclearError("Nuclear не вернул mcp-session-id — сервер MCP включён?")
        self.session_id = session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, method: str, params: dict | None = None):
        """Вызов Nuclear API: tools/call -> инструмент `call` -> Domain.method."""
        if not self.session_id:
            self.handshake()

        arguments: dict = {"method": method}
        if params is not None:
            arguments["params"] = params
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": "call", "arguments": arguments},
        }

        resp = self._post(body)
        if resp.status_code >= 400:
            # Сессия могла протухнуть (перезапуск Nuclear) — один повтор с новым handshake.
            self.handshake()
            resp = self._post(body)
            resp.raise_for_status()

        rpc = self._parse_rpc(resp)
        if "error" in rpc:
            raise NuclearError(rpc["error"].get("message", str(rpc["error"])))

        result = rpc.get("result", {})
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        if result.get("isError"):
            raise NuclearError(text or "Nuclear вернул ошибку без описания")
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


# ---------------------------------------------------------------------------
# Обёртки над Nuclear API — фиксированные цепочки вызовов
# ---------------------------------------------------------------------------

class Nuclear:
    def __init__(self, mcp: McpClient):
        self.mcp = mcp
        self._volume_is_unit: bool | None = None  # шкала громкости: True = 0-1, False = 0-100

    # -- поиск (идёт в активный metadata-провайдер, т.е. в плагин puer-ytmusic)

    def _search(self, query: str, kind: str, limit: int) -> list:
        result = self.mcp.call(
            "Metadata.search",
            # Параметр метода буквально называется `params` — вложенность не опечатка.
            {"params": {"query": query, "types": [kind], "limit": limit}},
        ) or {}
        return result.get(kind, []) or []

    # -- очередь и воспроизведение

    def replace_queue_and_play(self, tracks: list) -> None:
        self.mcp.call("Queue.clearQueue")
        self.mcp.call("Queue.addToQueue", {"tracks": tracks})
        self.mcp.call("Queue.goToIndex", {"index": 0})
        self.mcp.call("Playback.play")

    # -- команды-инструменты (каждая возвращает фразу для пользователя/озвучки)

    def play_track(self, query: str) -> str:
        tracks = self._search(query, "tracks", 5)
        if not tracks:
            return f"Ничего не нашёл по запросу «{query}»"
        self.replace_queue_and_play(tracks[:1])
        top = tracks[0]
        return f"Включаю: {_fmt_track(top)}"

    def play_artist(self, name: str) -> str:
        artists = self._search(name, "artists", 1)
        if not artists:
            return f"Исполнитель «{name}» не нашёлся"
        artist = artists[0]
        artist_id = artist["source"]["id"]
        tracks = self.mcp.call("Metadata.fetchArtistTopTracks", {"artistId": artist_id}) or []
        if not tracks:
            return f"У «{artist.get('name', name)}» не нашлось треков"
        self.replace_queue_and_play(tracks)
        return f"Включаю {artist.get('name', name)}: {len(tracks)} треков"

    def play_album(self, name: str) -> str:
        albums = self._search(name, "albums", 1)
        if not albums:
            return f"Альбом «{name}» не нашёлся"
        album_ref = albums[0]
        album = self.mcp.call(
            "Metadata.fetchAlbumDetails", {"albumId": album_ref["source"]["id"]},
        ) or {}
        tracks = album.get("tracks", [])
        if not tracks:
            return f"В альбоме «{album_ref.get('title', name)}» не нашлось треков"
        self.replace_queue_and_play(tracks)
        return f"Включаю альбом «{album.get('title', name)}»: {len(tracks)} треков"

    def play_playlist(self, name: str) -> str:
        # Сначала свои плейлисты в Nuclear, потом поиск в YouTube Music.
        # Свои сравниваем через транслитерацию: голосом приходит «жанулька»,
        # а плейлист называется «Zhanulka» — иначе свой не найдётся, и уедем
        # в YT Music на чужой одноимённый.
        wanted = name.lower().strip()
        index = self.mcp.call("Playlists.getIndex") or []
        local = next(
            (p for p in index if _playlist_names_match(wanted, p.get("name", ""))),
            None,
        )
        if local:
            playlist = self.mcp.call("Playlists.getPlaylist", {"id": local["id"]}) or {}
            tracks = [item["track"] for item in playlist.get("items", []) if item.get("track")]
            if tracks:
                self.replace_queue_and_play(tracks)
                return f"Включаю плейлист «{local['name']}»: {len(tracks)} треков"

        found = self._search(name, "playlists", 1)
        if not found:
            return f"Плейлист «{name}» не нашёлся ни в библиотеке, ни в YouTube Music"
        # У плейлистов нет своего fetch-метода — fetchAlbumDetails принимает их id (VL…/PL…).
        playlist = self.mcp.call(
            "Metadata.fetchAlbumDetails", {"albumId": found[0]["source"]["id"]},
        ) or {}
        tracks = playlist.get("tracks", [])
        if not tracks:
            return f"Плейлист «{found[0].get('name', name)}» оказался пустым"
        self.replace_queue_and_play(tracks)
        return f"Включаю плейлист «{playlist.get('title', name)}»: {len(tracks)} треков"

    def pause(self) -> str:
        self.mcp.call("Playback.pause")
        return "Пауза"

    def resume(self) -> str:
        self.mcp.call("Playback.play")
        return "Продолжаю"

    def stop(self) -> str:
        self.mcp.call("Playback.stop")
        return "Остановил"

    def next_track(self) -> str:
        self.mcp.call("Queue.goToNext")
        return "Следующий трек"

    def previous_track(self) -> str:
        self.mcp.call("Queue.goToPrevious")
        return "Предыдущий трек"

    def play_favorites(self) -> str:
        # getTracks возвращает обёртки FavoriteEntry {ref: Track, addedAtIso};
        # в очередь можно класть только сам трек из ref — обёртка роняет UI Nuclear.
        entries = self.mcp.call("Favorites.getTracks") or []
        tracks = [e["ref"] for e in entries if isinstance(e, dict) and e.get("ref")]
        if not tracks:
            return "В избранном пока пусто"
        self.replace_queue_and_play(tracks)
        return f"Включаю избранное: {len(tracks)} треков"

    def favorite_current(self) -> str:
        item = self.mcp.call("Queue.getCurrentItem")
        if not item or not item.get("track"):
            return "Сейчас ничего не играет"
        self.mcp.call("Favorites.addTrack", {"track": item["track"]})
        return f"Добавил в избранное: {_fmt_track(item['track'])}"

    def now_playing(self) -> str:
        item = self.mcp.call("Queue.getCurrentItem")
        if not item or not item.get("track"):
            return "Сейчас ничего не играет"
        state = self.mcp.call("Playback.getState") or {}
        status = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(state.get("status"), "")
        position = ""
        if state.get("duration"):
            position = f" ({_fmt_time(state.get('seek', 0))} / {_fmt_time(state['duration'])})"
        return f"{status} {_fmt_track(item['track'])}{position}".strip()

    def set_shuffle(self, enabled: bool) -> str:
        self.mcp.call("Playback.setShuffleEnabled", {"enabled": enabled})
        return "Перемешиваю" if enabled else "Играю по порядку"

    def set_volume(self, level: int) -> str:
        level = max(0, min(100, int(level)))
        self.mcp.call("Playback.setVolume", {"volume": self._to_volume_scale(level)})
        return f"Громкость {level}%"

    def change_volume(self, delta: int) -> str:
        current = self.mcp.call("Playback.getVolume") or 0
        if isinstance(current, (int, float)):
            self._volume_is_unit = current <= 1
        current_pct = current * 100 if self._volume_is_unit else current
        return self.set_volume(int(round(current_pct)) + delta)

    def _to_volume_scale(self, level_pct: int) -> float | int:
        """Шкала громкости Nuclear не задокументирована: подстраиваемся под текущее
        значение. Определяем один раз и кешируем — дальше без лишнего запроса."""
        if self._volume_is_unit is None:
            current = self.mcp.call("Playback.getVolume")
            self._volume_is_unit = isinstance(current, (int, float)) and current <= 1
        if self._volume_is_unit:
            return round(level_pct / 100, 2)
        return level_pct


# ---------------------------------------------------------------------------
# Интернет-инструменты (не про музыку)
# ---------------------------------------------------------------------------

_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
               "августа", "сентября", "октября", "ноября", "декабря"]
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница",
             "суббота", "воскресенье"]


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def get_time() -> str:
    """Текущее время словами — «Сейчас 14 часов 35 минут» (удобно озвучивать)."""
    from datetime import datetime

    now = datetime.now()
    hours = f"{now.hour} {_plural(now.hour, 'час', 'часа', 'часов')}"
    if now.minute == 0:
        return f"Сейчас ровно {hours}"
    minutes = f"{now.minute} {_plural(now.minute, 'минута', 'минуты', 'минут')}"
    return f"Сейчас {hours} {minutes}"


def get_date() -> str:
    """Сегодняшняя дата и день недели — «Сегодня 1 августа, суббота»."""
    from datetime import datetime

    now = datetime.now()
    return f"Сегодня {now.day} {_MONTHS_GEN[now.month - 1]}, {_WEEKDAYS[now.weekday()]}"


def web_search(query: str, limit: int = 5) -> list[tuple[str, str]]:
    """Поиск в DuckDuckGo (HTML-версия, без API-ключа) -> [(заголовок, сниппет)]."""
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "ru-ru"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
    results = []
    for i, title in enumerate(titles[:limit]):
        snippet = snippets[i] if i < len(snippets) else ""
        results.append((_strip_html(title), _strip_html(snippet)))
    return results


def _strip_html(text: str) -> str:
    import html

    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _strip_think(text: str) -> str:
    """Вырезать рассуждения qwen3. Даже с think:false модель иногда рассуждает,
    причём открывающий <think> может отсутствовать — режем и одинокие теги."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:  # рассуждения без открывающего тега — ответ после него
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:  # открыли и не закрыли — ответа не было
        text = text.split("<think>", 1)[0]
    return text.strip()


# Расшифровка кодов погоды WMO, которые отдаёт Open-Meteo.
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

_geo_cache: dict[str, tuple[float, float, str]] = {}


def _geocode(city: str) -> tuple[float, float, str]:
    """Город -> координаты через геокодер Open-Meteo (кешируется на сессию).

    Whisper отдаёт город в падеже («в казани») — пробуем восстановить
    именительный простыми заменами окончания.
    """
    if city in _geo_cache:
        return _geo_cache[city]
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
            _geo_cache[city] = found
            return found
    raise ValueError(f"город «{city}» не нашёлся")


def _locate_by_ip() -> tuple[float, float, str]:
    resp = requests.get("http://ip-api.com/json/?fields=lat,lon,city&lang=ru", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["lat"], data["lon"], data.get("city", "")


def get_weather(city: str = "") -> str:
    """Погода через Open-Meteo — бесплатно, без API-ключа, отвечает за ~0.3 с.

    Город: из команды -> WEATHER_CITY из конфига -> по IP (ip-api.com).
    Фраза собирается «под озвучку»: температура словами (плюс/минус N).
    """
    city = (city or WEATHER_CITY).strip()
    try:
        lat, lon, name = _geocode(city) if city else _locate_by_ip()
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
    phrase = f"{place} {_spoken_temp(current['temperature_2m'])}"
    desc = _WMO_DESC.get(current.get("weather_code"))
    if desc:
        phrase += f", {desc}"
    phrase += f", ощущается как {_spoken_temp(current['apparent_temperature'])}"
    daily = data.get("daily") or {}
    if daily.get("temperature_2m_min") and daily.get("temperature_2m_max"):
        low = _spoken_temp(daily["temperature_2m_min"][0], genitive=True)
        high = _spoken_temp(daily["temperature_2m_max"][0], genitive=True)
        phrase += f". Сегодня от {low} до {high}"
    return phrase


def _spoken_temp(value, genitive: bool = False) -> str:
    degrees = int(round(float(value)))
    if degrees > 0:
        return f"плюс {degrees}"
    if degrees < 0:
        return f"минус {abs(degrees)}"
    return "нуля" if genitive else "ноль"


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    # Нормализация латиницы, чтобы «рок» сошёлся с "Rock": ck -> k, c -> k (кроме ch).
    text = text.replace("ck", "k")
    return re.sub(r"c(?!h)", "k", text)


def _playlist_names_match(wanted: str, name: str) -> bool:
    """Нечёткое совпадение имён плейлистов: кириллица ↔ латиница, падежи."""
    w, n = _translit(wanted), _translit(name.lower().strip())
    if not w or not n:
        return False
    if w in n or n in w:
        return True
    # «жанульку» -> «жанульк» ⊂ «zhanulka»: терпимость к падежному окончанию.
    return len(w) > 3 and w[:-1] in n


def _fmt_track(track: dict) -> str:
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    title = track.get("title", "?")
    return f"{artists} — {title}" if artists else title


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Быстрый роутер: частые команды мимо LLM (мгновенно; на Pi это 80% команд)
# ---------------------------------------------------------------------------

def build_router(player: Nuclear) -> list[tuple[re.Pattern, callable]]:
    def volume_cmd(match: re.Match) -> str:
        return player.set_volume(int(match.group(1)))

    rules: list[tuple[str, callable]] = [
        (r"^(пауза|стоп музыка|подожди|pause)$", lambda m: player.pause()),
        # «замолчи»/«заткнись» тут нет намеренно — они затыкают озвучку (voice.py),
        # а не музыку.
        (r"^(стоп|стой|остановись|хватит|выключи|stop)$", lambda m: player.stop()),
        (r"^(играй|продолжи|продолжай|воспроизведи|play|плей)$", lambda m: player.resume()),
        # «Включи музыку» без уточнений = продолжить то, что в очереди.
        (r"^(?:включ\w+|поставь|играй|запусти)\s+музыку$", lambda m: player.resume()),
        (r"^(дальше|следующ\w*|скип|пропусти|next|skip)$", lambda m: player.next_track()),
        (r"^(назад|предыдущ\w*|prev|back)$", lambda m: player.previous_track()),
        (r"^(громче|погромче)$", lambda m: player.change_volume(+10)),
        (r"^(тише|потише)$", lambda m: player.change_volume(-10)),
        (r"^громкость\s+(\d{1,3})", volume_cmd),
        (r"^(что играет|что сейчас играет|now playing)\??$", lambda m: player.now_playing()),
        (r"^(в избранное|лайк|нравится|сохрани)$", lambda m: player.favorite_current()),
        (r"^(?:(?:включ\w+|поставь|запусти)\s+)?(?:избранн\w+|любим\w+)(?:\s+(?:треки|музыку|песни))?$",
         lambda m: player.play_favorites()),
        # «все песни из Х» (саундтрек, не исполнитель) — пусть решает LLM.
        (r"^(?:(?:включ\w+|поставь|запусти)\s+)?все (?:песни|треки) (?!из\s)(.+)$",
         lambda m: player.play_artist(m.group(1))),
        # Явно названный тип — детерминированно, без LLM (экономит секунды на команду).
        (r"^(?:(?:включ\w+|поставь|запусти)\s+)?альбом\s+(.+)$",
         lambda m: player.play_album(m.group(1))),
        (r"^(?:(?:включ\w+|поставь|запусти)\s+)?плейлист\s+(.+)$",
         lambda m: player.play_playlist(m.group(1))),
        (r"^(?:включ\w+|поставь|запусти)\s+(?:трек|песню)\s+(.+)$",
         lambda m: player.play_track(m.group(1))),
        (r"^(?:включ\w+|поставь|запусти)\s+(?:группу|исполнителя|артиста)\s+(.+)$",
         lambda m: player.play_artist(m.group(1))),
        (r"^(?:а\s+)?(?:сколько\s+(?:сейчас\s+)?(?:времени|время)|который\s+час)(?:\s+на часах)?\s*\??$",
         lambda m: get_time()),
        (r"^(?:а\s+)?(?:какое\s+(?:сегодня\s+)?число|какой\s+(?:сегодня\s+)?день(?:\s+недели)?)\s*\??$",
         lambda m: get_date()),
        (r"^(?:какая\s+)?(?:сейчас\s+)?погода(?:\s+(?:сейчас|сегодня))?(?:\s+(?:в|на)\s+(.+?))?\s*\??$",
         lambda m: get_weather(m.group(1) or "")),
        (r"^сколько (?:сейчас )?градусов(?: на улице)?\??$", lambda m: get_weather("")),
        (r"^(перемешай|шафл|shuffle)$", lambda m: player.set_shuffle(True)),
        (r"^(по порядку|без шафла)$", lambda m: player.set_shuffle(False)),
    ]
    return [(re.compile(pattern, re.IGNORECASE), fn) for pattern, fn in rules]


# ---------------------------------------------------------------------------
# LLM через Ollama: инструменты для всего, что не поймал роутер
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Ты — голосовой помощник музыкального плеера. На каждую команду пользователя "
    "вызови ровно один подходящий инструмент. Названия песен, исполнителей и плейлистов "
    "передавай так, как их произнёс пользователь, не переводя на другой язык. "
    "На вопрос о погоде вызови get_weather, о времени — get_time, о дате — "
    "get_date. На вопрос о фактах, людях, событиях или новостях вызови "
    "web_search. Если это просто болтовня — ответь одной короткой фразой "
    "без инструментов."
)

def build_llm_tools() -> list[dict]:
    def tool(name: str, description: str, params: dict | None = None, required: list | None = None) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params or {},
                    "required": required or [],
                },
            },
        }

    query = {"type": "string", "description": "Название так, как сказал пользователь"}
    return [
        tool("play_track", "Найти и включить конкретную песню", {"query": query}, ["query"]),
        tool("play_artist", "Включить песни исполнителя («включи X», «все песни X»)",
             {"name": query}, ["name"]),
        tool("play_album", "Найти и включить альбом целиком", {"name": query}, ["name"]),
        tool("play_playlist", "Включить плейлист по названию", {"name": query}, ["name"]),
        tool("pause", "Поставить на паузу"),
        tool("resume", "Продолжить воспроизведение"),
        tool("next_track", "Переключить на следующий трек"),
        tool("previous_track", "Вернуться к предыдущему треку"),
        tool("favorite_current", "Добавить текущий играющий трек в избранное"),
        tool("play_favorites", "Включить сохранённое избранное (только «включи избранное/любимое», "
                               "НЕ для песен конкретного исполнителя)"),
        tool("now_playing", "Сказать, что сейчас играет"),
        tool("set_volume", "Установить громкость в процентах",
             {"level": {"type": "integer", "description": "0-100"}}, ["level"]),
        tool("get_weather", "Узнать погоду сейчас и на сегодня",
             {"city": {"type": "string", "description": "Город, если назван; иначе пустая строка"}}),
        tool("web_search", "Найти в интернете ответ на вопрос о фактах, людях, событиях, новостях",
             {"query": {"type": "string", "description": "Поисковый запрос"}}, ["query"]),
        tool("get_time", "Сказать текущее время"),
        tool("get_date", "Сказать сегодняшнюю дату и день недели"),
    ]


class Agent:
    def __init__(self, player: Nuclear):
        self.player = player
        self.router = build_router(player)
        self.tools = build_llm_tools()
        self.tool_impl = {
            "play_track": lambda a: player.play_track(a["query"]),
            "play_artist": lambda a: player.play_artist(a["name"]),
            "play_album": lambda a: player.play_album(a["name"]),
            "play_playlist": lambda a: player.play_playlist(a["name"]),
            "pause": lambda a: player.pause(),
            "resume": lambda a: player.resume(),
            "next_track": lambda a: player.next_track(),
            "previous_track": lambda a: player.previous_track(),
            "favorite_current": lambda a: player.favorite_current(),
            "play_favorites": lambda a: player.play_favorites(),
            "now_playing": lambda a: player.now_playing(),
            "set_volume": lambda a: player.set_volume(int(a["level"])),
            "get_weather": lambda a: get_weather(a.get("city") or ""),
            "web_search": lambda a: self.answer_from_web(a["query"]),
            "get_time": lambda a: get_time(),
            "get_date": lambda a: get_date(),
        }
        self._think_supported = True
        self._http = requests.Session()
        # «Найди/загугли X» — поиск напрямую, без выбора инструмента моделью.
        self.router.append((
            re.compile(r"^(?:найди|загугли|погугли|поищи)\s+(.+)$", re.IGNORECASE),
            lambda m: self.answer_from_web(m.group(1)),
        ))

    def warmup_async(self) -> None:
        """Грузим модель в память Ollama в фоне, чтобы первая LLM-команда не платила
        холодный старт (несколько секунд). Пустой messages — штатный способ load."""
        def _load():
            try:
                self._http.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": OLLAMA_MODEL, "messages": [], "keep_alive": OLLAMA_KEEP_ALIVE},
                    timeout=HTTP_TIMEOUT,
                )
            except requests.RequestException:
                pass  # недоступность Ollama всплывёт с нормальной ошибкой на первой команде
        threading.Thread(target=_load, daemon=True).start()

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        for pattern, fn in self.router:
            match = pattern.match(text)
            if match:
                return fn(match)
        return self._handle_with_llm(text)

    def answer_from_web(self, query: str) -> str:
        """Поиск в интернете + краткий пересказ результатов моделью.

        Единственное место с двумя вызовами LLM на команду: без второго круга
        пользователю пришлось бы слушать сырые сниппеты поисковика.
        """
        try:
            results = web_search(query)
        except requests.RequestException as error:
            return f"Поиск не удался: {error}"
        if not results:
            return f"По запросу «{query}» ничего не нашлось"
        context = "\n".join(f"- {title}. {snippet}" for title, snippet in results)
        try:
            reply = self._ollama_raw([
                {"role": "system", "content":
                    "Ответь на вопрос пользователя по результатам поиска: кратко, "
                    "1-3 предложения, по-русски, без ссылок и лишних слов — ответ "
                    "будет озвучен голосом. Отвечай сразу, без рассуждений."},
                # /no_think — переключатель qwen3, глушит режим рассуждений.
                {"role": "user",
                 "content": f"Вопрос: {query}\n\nРезультаты поиска:\n{context} /no_think"},
            ])
            content = _strip_think(reply.get("message", {}).get("content") or "")
            if content:
                return content
        except requests.RequestException:
            pass
        # LLM недоступна/промолчала — отдаём хотя бы первый сниппет.
        title, snippet = results[0]
        return snippet or title

    def _ollama_chat(self, text: str) -> dict:
        return self._ollama_raw(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=self.tools,
        )

    def _ollama_raw(self, messages: list[dict], tools: list | None = None) -> dict:
        body = {
            "model": OLLAMA_MODEL,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "messages": messages,
            "options": {"temperature": 0},
        }
        if tools:
            body["tools"] = tools
        if self._think_supported:
            body["think"] = False  # для qwen3: рассуждения дают +секунды латентности
        resp = self._http.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=HTTP_TIMEOUT)
        if resp.status_code == 400 and self._think_supported and "think" in resp.text.lower():
            self._think_supported = False
            return self._ollama_raw(messages, tools)
        resp.raise_for_status()
        return resp.json()

    def _handle_with_llm(self, text: str) -> str:
        reply = self._ollama_chat(text)
        message = reply.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = _strip_think(message.get("content") or "")
            # qwen3:1.7b иногда пишет tool call текстом вместо настоящего вызова.
            text_call = _parse_text_tool_call(content)
            if text_call:
                tool_calls = [text_call]
            else:
                return content or "Не понял команду"

        results = []
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            impl = self.tool_impl.get(name)
            if not impl:
                results.append(f"Неизвестный инструмент: {name}")
                continue
            try:
                results.append(impl(arguments))
            except NuclearError as error:
                results.append(f"Nuclear: {error}")
        return "; ".join(r for r in results if r)


def _parse_text_tool_call(content: str) -> dict | None:
    """{"name": ..., "arguments": {...}} в тексте ответа -> формат tool_calls."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "name" not in obj:
        return None
    arguments = obj.get("arguments") or obj.get("parameters") or {}
    return {"function": {"name": obj["name"], "arguments": arguments}}


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def check_connections(mcp: McpClient) -> None:
    try:
        mcp.handshake()
        state = mcp.call("Playback.getState")
        print(f"✔ Nuclear MCP: {NUCLEAR_MCP_URL} (playback: {state.get('status', '?')})")
    except Exception as error:
        print(f"✘ Nuclear MCP недоступен ({NUCLEAR_MCP_URL}): {error}")
        print("  Проверь: Nuclear запущен, Settings → Integrations → Enable MCP Server.")
        sys.exit(1)
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/version", timeout=5)
        print(f"✔ Ollama {resp.json().get('version', '?')}: модель {OLLAMA_MODEL}")
    except Exception as error:
        print(f"✘ Ollama недоступна ({OLLAMA_URL}): {error}")
        print("  Запусти Ollama и выполни: ollama pull " + OLLAMA_MODEL)
        sys.exit(1)


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    mcp = McpClient(NUCLEAR_MCP_URL)
    player = Nuclear(mcp)
    agent = Agent(player)

    print("Nuclear CLI AI — этап 1 (текстовые команды)")
    check_connections(mcp)
    agent.warmup_async()  # модель грузится, пока пользователь печатает первую команду
    print("Примеры: «включи нирвану», «поставь smells like teen spirit», «плейлист rock»,")
    print("         «дальше», «пауза», «громче», «что играет», «в избранное». Выход: q\n")

    while True:
        try:
            text = input("🎤 > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.strip().lower() in ("q", "quit", "exit", "выход"):
            break
        started = time.monotonic()
        try:
            answer = agent.handle(text)
        except NuclearError as error:
            answer = f"Nuclear: {error}"
        except requests.RequestException as error:
            answer = f"Сеть: {error}"
        elapsed = time.monotonic() - started
        if answer:
            print(f"   {answer}   [{elapsed:.1f}s]")


if __name__ == "__main__":
    main()
