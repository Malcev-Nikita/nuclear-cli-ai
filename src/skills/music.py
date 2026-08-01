"""Музыка из YT Music: треки, исполнители, альбомы, плейлисты.

Не нашлось в музыке — фолбэк на обычный YouTube (ютуберы, подкасты).
"""

from __future__ import annotations

import re

import requests

from src.core.texts import fmt_track
from src.services.nuclear import NuclearPlayer
from src.skills.base import QUERY_PARAM, Rule, Skill, Tool
from src.skills.youtube import YoutubeSkill

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = text.replace("ck", "k")  # чтобы «рок» сошёлся с "Rock"
    return re.sub(r"c(?!h)", "k", text)


def playlist_names_match(wanted: str, name: str) -> bool:
    """Нечёткое совпадение имён плейлистов: кириллица ↔ латиница, падежи."""
    w, n = translit(wanted), translit(name.lower().strip())
    if not w or not n:
        return False
    if w in n or n in w:
        return True
    return len(w) > 3 and w[:-1] in n  # «жанульку» -> «жанульк» ⊂ «zhanulka»


class MusicSkill(Skill):
    def __init__(self, player: NuclearPlayer, youtube: YoutubeSkill):
        self.player = player
        self.youtube = youtube

    def _youtube_fallback(self, query: str) -> str:
        try:
            return self.youtube.play(query)
        except requests.RequestException:
            return f"Ничего не нашёл по запросу «{query}»"

    def play_track(self, query: str) -> str:
        tracks = self.player.search(query, "tracks", 5)
        if not tracks:
            return self._youtube_fallback(query)
        self.player.replace_queue_and_play(tracks[:1])
        return f"Включаю: {fmt_track(tracks[0])}"

    def play_artist(self, name: str) -> str:
        artists = self.player.search(name, "artists", 1)
        if not artists:
            return self._youtube_fallback(name)
        artist = artists[0]
        tracks = self.player.artist_top_tracks(artist["source"]["id"])
        if not tracks:
            return f"У «{artist.get('name', name)}» не нашлось треков"
        self.player.replace_queue_and_play(tracks)
        return f"Включаю {artist.get('name', name)}: {len(tracks)} треков"

    def play_album(self, name: str) -> str:
        albums = self.player.search(name, "albums", 1)
        if not albums:
            return f"Альбом «{name}» не нашёлся"
        album = self.player.album_details(albums[0]["source"]["id"])
        tracks = album.get("tracks", [])
        if not tracks:
            return f"В альбоме «{albums[0].get('title', name)}» не нашлось треков"
        self.player.replace_queue_and_play(tracks)
        return f"Включаю альбом «{album.get('title', name)}»: {len(tracks)} треков"

    def play_playlist(self, name: str) -> str:
        # Сначала свои плейлисты (сравнение с транслитом: голосом приходит
        # «жанулька», а плейлист называется «Zhanulka»), потом YT Music.
        wanted = name.lower().strip()
        local = next(
            (p for p in self.player.playlists_index()
             if playlist_names_match(wanted, p.get("name", ""))),
            None,
        )
        if local:
            tracks = self.player.playlist_tracks(local["id"])
            if tracks:
                self.player.replace_queue_and_play(tracks)
                return f"Включаю плейлист «{local['name']}»: {len(tracks)} треков"

        found = self.player.search(name, "playlists", 1)
        if not found:
            return f"Плейлист «{name}» не нашёлся ни в библиотеке, ни в YouTube Music"
        playlist = self.player.album_details(found[0]["source"]["id"])
        tracks = playlist.get("tracks", [])
        if not tracks:
            return f"Плейлист «{found[0].get('name', name)}» оказался пустым"
        self.player.replace_queue_and_play(tracks)
        return f"Включаю плейлист «{playlist.get('title', name)}»: {len(tracks)} треков"

    def rules(self) -> list[Rule]:
        return [
            # «все песни из Х» (саундтрек, не исполнитель) — пусть решает LLM
            Rule(r"^(?:(?:включ\w+|поставь|запусти)\s+)?все (?:песни|треки) (?!из\s)(.+)$",
                 lambda m: self.play_artist(m.group(1))),
            Rule(r"^(?:(?:включ\w+|поставь|запусти)\s+)?альбом\s+(.+)$",
                 lambda m: self.play_album(m.group(1))),
            Rule(r"^(?:(?:включ\w+|поставь|запусти)\s+)?плейлист\s+(.+)$",
                 lambda m: self.play_playlist(m.group(1))),
            Rule(r"^(?:включ\w+|поставь|запусти)\s+(?:трек|песню)\s+(.+)$",
                 lambda m: self.play_track(m.group(1))),
            Rule(r"^(?:включ\w+|поставь|запусти)\s+(?:группу|исполнителя|артиста)\s+(.+)$",
                 lambda m: self.play_artist(m.group(1))),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("play_track", "Найти и включить конкретную песню",
                 lambda a: self.play_track(a["query"]),
                 params={"query": QUERY_PARAM}, required=["query"], query_arg="query"),
            Tool("play_artist", "Включить песни исполнителя («включи X», «все песни X»)",
                 lambda a: self.play_artist(a["name"]),
                 params={"name": QUERY_PARAM}, required=["name"], query_arg="name"),
            Tool("play_album", "Найти и включить альбом целиком",
                 lambda a: self.play_album(a["name"]),
                 params={"name": QUERY_PARAM}, required=["name"], query_arg="name"),
            Tool("play_playlist", "Включить плейлист по названию",
                 lambda a: self.play_playlist(a["name"]),
                 params={"name": QUERY_PARAM}, required=["name"], query_arg="name"),
        ]
