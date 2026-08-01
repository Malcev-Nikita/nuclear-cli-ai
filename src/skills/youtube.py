"""Обычный YouTube (не YT Music): видосы, миксы, подкасты — в Nuclear идёт звук."""

from __future__ import annotations

from src.services.nuclear import NuclearPlayer
from src.services.youtube import YoutubeSearch
from src.skills.base import QUERY_PARAM, Rule, Skill, Tool


class YoutubeSkill(Skill):
    def __init__(self, player: NuclearPlayer, search: YoutubeSearch):
        self.player = player
        self.search = search

    def play(self, query: str) -> str:
        videos = self.search.search(query)
        if not videos:
            return f"На ютубе ничего не нашлось по запросу «{query}»"
        video = videos[0]
        # Формат трека сверен с плагином (playlist.ts): artists с roles,
        # source.provider "youtube" -> плагин стримит точный видео-ID.
        track = {
            "title": video["title"],
            "artists": [{"name": video["channel"] or "YouTube", "roles": ["main"]}],
            "source": {
                "provider": "youtube",
                "id": video["videoId"],
                "url": f"https://www.youtube.com/watch?v={video['videoId']}",
            },
        }
        if video.get("durationMs"):
            track["durationMs"] = video["durationMs"]
        self.player.replace_queue_and_play([track])
        return f"Включаю с ютуба: {video['title']}"

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:включ\w+|поставь|запусти)\s+(?:с|из)\s+ют[юу]б\w*\s+(.+)$",
                 lambda m: self.play(m.group(1))),
            Rule(r"^(?:включ\w+|поставь|запусти)\s+(?:видео|видос\w*|ролик)\s+(.+?)(?:\s+(?:с|на|из)\s+ют[юу]б\w*)?$",
                 lambda m: self.play(m.group(1))),
            Rule(r"^(?:включ\w+|поставь|запусти)\s+(.+?)\s+(?:с|из|на)\s+ют[юу]б\w*$",
                 lambda m: self.play(m.group(1))),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("play_youtube", "Включить видео/ролик/микс/подкаст с обычного YouTube "
                                 "(когда просят именно ютуб или видео, а не песню)",
                 lambda a: self.play(a["query"]),
                 params={"query": QUERY_PARAM}, required=["query"], query_arg="query"),
        ]
