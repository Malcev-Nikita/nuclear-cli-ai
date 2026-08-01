#!/usr/bin/env python3
"""Nuclear CLI AI — этап 1: текстовый агент для управления Nuclear голосовыми командами.

Цепочка: команда → быстрый regex-роутер → (если не распознано) LLM через Ollama
с узкими инструментами → MCP-сервер Nuclear → плеер.

Запуск:  python assistant.py
Конфиг:  переменные окружения NUCLEAR_MCP_URL, OLLAMA_URL, OLLAMA_MODEL.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

import requests

NUCLEAR_MCP_URL = os.environ.get("NUCLEAR_MCP_URL", "http://127.0.0.1:8800/mcp")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")
# Держим модель в памяти между командами, иначе каждый запрос платит холодный старт.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")

HTTP_TIMEOUT = 90  # поиск идёт через InnerTube/yt-dlp, первые запросы бывают долгими
WEATHER_CITY = os.environ.get("WEATHER_CITY", "")  # пусто = wttr.in определит по IP


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
        wanted = name.lower().strip()
        index = self.mcp.call("Playlists.getIndex") or []
        local = next((p for p in index if wanted in p.get("name", "").lower()), None)
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

def get_weather(city: str = "") -> str:
    """Погода через wttr.in — без API-ключа; без города определяет его по IP.

    Фраза собирается «под озвучку»: температура словами (плюс/минус N).
    """
    city = (city or WEATHER_CITY).strip()
    try:
        resp = requests.get(
            f"https://wttr.in/{requests.utils.quote(city)}?format=j1&lang=ru",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        current = data["current_condition"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        return f"Не смог узнать погоду: {error}"

    desc = (current.get("lang_ru") or current.get("weatherDesc") or [{}])[0].get("value", "")
    if not city:
        try:
            city = data["nearest_area"][0]["areaName"][0]["value"]
        except (KeyError, IndexError):
            pass

    place = f" в {city}" if city else ""
    phrase = f"Сейчас{place} {_spoken_temp(current['temp_C'])}"
    if desc:
        phrase += f", {desc.lower()}"
    phrase += f", ощущается как {_spoken_temp(current['FeelsLikeC'])}"
    today = (data.get("weather") or [{}])[0]
    if today.get("mintempC") and today.get("maxtempC"):
        low = _spoken_temp(today["mintempC"], genitive=True)
        high = _spoken_temp(today["maxtempC"], genitive=True)
        phrase += f". Сегодня от {low} до {high}"
    return phrase


def _spoken_temp(value, genitive: bool = False) -> str:
    degrees = int(value)
    if degrees > 0:
        return f"плюс {degrees}"
    if degrees < 0:
        return f"минус {abs(degrees)}"
    return "нуля" if genitive else "ноль"


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
        (r"^(стоп|стой|остановись|хватит|замолчи|выключи|stop)$", lambda m: player.stop()),
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
    "На вопрос о погоде вызови get_weather. Если команда не про музыку и не про "
    "погоду — ответь одной короткой фразой без инструментов."
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
        }
        self._think_supported = True
        self._http = requests.Session()

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

    def _ollama_chat(self, text: str) -> dict:
        body = {
            "model": OLLAMA_MODEL,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "tools": self.tools,
            "options": {"temperature": 0},
        }
        if self._think_supported:
            body["think"] = False  # для qwen3: рассуждения дают +секунды латентности
        resp = self._http.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=HTTP_TIMEOUT)
        if resp.status_code == 400 and self._think_supported and "think" in resp.text.lower():
            self._think_supported = False
            return self._ollama_chat(text)
        resp.raise_for_status()
        return resp.json()

    def _handle_with_llm(self, text: str) -> str:
        reply = self._ollama_chat(text)
        message = reply.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = (message.get("content") or "").strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
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
