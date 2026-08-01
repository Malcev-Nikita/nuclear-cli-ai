"""Nuclear через MCP: клиент протокола + механика плеера.

Все грабли Nuclear (SSE, кириллица, форматы) инкапсулированы здесь;
наружу — чистые данные, фразы для пользователя собирают навыки.
"""

from __future__ import annotations

import json

import requests

from config import HTTP_TIMEOUT, NUCLEAR_MCP_URL


class NuclearError(Exception):
    pass


class McpClient:
    """Streamable-HTTP клиент MCP Nuclear."""

    def __init__(self, url: str = NUCLEAR_MCP_URL):
        self.url = url
        self.session_id: str | None = None
        self._id = 0
        self._http = requests.Session()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",  # без обоих типов — 406
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
        # Декодируем сами: SSE приходит без charset, requests взял бы latin-1.
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
            raise NuclearError(f"В SSE-ответе нет JSON-RPC сообщения: {text[:200]}")
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
                "clientInfo": {"name": "nuclear-cli-ai", "version": "0.4"},
            },
        })
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            raise NuclearError("Nuclear не вернул mcp-session-id — сервер MCP включён?")
        self.session_id = session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, method: str, params: dict | None = None):
        """tools/call -> инструмент `call` -> Domain.method."""
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
            self.handshake()  # сессия протухла (перезапуск Nuclear) — один повтор
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


class NuclearPlayer:
    """Механика плеера поверх MCP. Только данные и действия — без фраз."""

    def __init__(self, mcp: McpClient):
        self.mcp = mcp
        self._volume_is_unit: bool | None = None  # шкала: True = 0-1, False = 0-100

    # -- поиск (идёт в активный metadata-провайдер, т.е. в плагин puer-ytmusic)

    def search(self, query: str, kind: str, limit: int) -> list:
        result = self.mcp.call(
            "Metadata.search",
            {"params": {"query": query, "types": [kind], "limit": limit}},  # двойной params — не опечатка
        ) or {}
        return result.get(kind, []) or []

    def artist_top_tracks(self, artist_id: str) -> list:
        return self.mcp.call("Metadata.fetchArtistTopTracks", {"artistId": artist_id}) or []

    def album_details(self, album_id: str) -> dict:
        # Принимает и id плейлистов (VL…/PL…) — своего fetch-метода у них нет.
        return self.mcp.call("Metadata.fetchAlbumDetails", {"albumId": album_id}) or {}

    # -- очередь и воспроизведение

    def replace_queue_and_play(self, tracks: list) -> None:
        self.mcp.call("Queue.clearQueue")
        self.mcp.call("Queue.addToQueue", {"tracks": tracks})
        self.mcp.call("Queue.goToIndex", {"index": 0})
        self.mcp.call("Playback.play")

    def pause(self) -> None:
        self.mcp.call("Playback.pause")

    def resume(self) -> None:
        self.mcp.call("Playback.play")

    def stop(self) -> None:
        self.mcp.call("Playback.stop")

    def next_track(self) -> None:
        self.mcp.call("Queue.goToNext")

    def previous_track(self) -> None:
        self.mcp.call("Queue.goToPrevious")

    def state(self) -> dict:
        return self.mcp.call("Playback.getState") or {}

    def seek_to(self, seconds: float) -> None:
        self.mcp.call("Playback.seekTo", {"seconds": seconds})

    def current_track(self) -> dict | None:
        item = self.mcp.call("Queue.getCurrentItem")
        return item.get("track") if item else None

    def set_shuffle(self, enabled: bool) -> None:
        self.mcp.call("Playback.setShuffleEnabled", {"enabled": enabled})

    # -- избранное

    def add_favorite(self, track: dict) -> None:
        self.mcp.call("Favorites.addTrack", {"track": track})

    def favorite_tracks(self) -> list:
        # getTracks отдаёт обёртки FavoriteEntry {ref, addedAtIso}; в очередь
        # годится только ref — сырая обёртка роняет рендерер Nuclear.
        entries = self.mcp.call("Favorites.getTracks") or []
        return [e["ref"] for e in entries if isinstance(e, dict) and e.get("ref")]

    # -- плейлисты

    def playlists_index(self) -> list:
        return self.mcp.call("Playlists.getIndex") or []

    def playlist_tracks(self, playlist_id) -> list:
        playlist = self.mcp.call("Playlists.getPlaylist", {"id": playlist_id}) or {}
        return [item["track"] for item in playlist.get("items", []) if item.get("track")]

    # -- громкость (шкала не задокументирована — определяется по факту и кешируется)

    def volume_pct(self) -> int:
        current = self.mcp.call("Playback.getVolume") or 0
        if isinstance(current, (int, float)) and current <= 1:
            return int(round(current * 100))
        return int(round(current))

    def set_volume_pct(self, level: int) -> None:
        if self._volume_is_unit is None:
            current = self.mcp.call("Playback.getVolume")
            self._volume_is_unit = isinstance(current, (int, float)) and current <= 1
        value = round(level / 100, 2) if self._volume_is_unit else level
        self.mcp.call("Playback.setVolume", {"volume": value})
