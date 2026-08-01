"""Поиск по обычному YouTube: InnerTube WEB — внутренний API сайта, без ключа."""

from __future__ import annotations

import requests


def _walk_json(obj, key: str):
    """Все значения по ключу на любой глубине вложенного JSON."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for value in obj.values():
            yield from _walk_json(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_json(value, key)


def duration_ms(text: str | None) -> int | None:
    if not text:
        return None  # у лайв-стримов длительности нет
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds * 1000


class YoutubeSearch:
    def search(self, query: str, limit: int = 5) -> list[dict]:
        resp = requests.post(
            "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
            json={
                "context": {"client": {"clientName": "WEB", "clientVersion": "2.20250101.00.00"}},
                "query": query,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        videos = []
        for renderer in _walk_json(resp.json(), "videoRenderer"):
            try:
                video = {
                    "videoId": renderer["videoId"],
                    "title": "".join(r["text"] for r in renderer["title"]["runs"]),
                    "channel": renderer.get("ownerText", {}).get("runs", [{}])[0].get("text", ""),
                    "durationMs": duration_ms(renderer.get("lengthText", {}).get("simpleText")),
                }
            except (KeyError, IndexError):
                continue
            videos.append(video)
            if len(videos) >= limit:
                break
        return videos
