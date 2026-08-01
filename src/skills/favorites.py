"""Избранное: лайкнуть текущий трек, включить все лайки."""

from __future__ import annotations

from src.core.texts import fmt_track
from src.services.nuclear import NuclearPlayer
from src.skills.base import Rule, Skill, Tool


class FavoritesSkill(Skill):
    def __init__(self, player: NuclearPlayer):
        self.player = player

    def favorite_current(self) -> str:
        track = self.player.current_track()
        if not track:
            return "Сейчас ничего не играет"
        self.player.add_favorite(track)
        return f"Добавил в избранное: {fmt_track(track)}"

    def play_favorites(self) -> str:
        tracks = self.player.favorite_tracks()
        if not tracks:
            return "В избранном пока пусто"
        self.player.replace_queue_and_play(tracks)
        return f"Включаю избранное: {len(tracks)} треков"

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(в избранное|лайк|нравится|сохрани)$", lambda m: self.favorite_current()),
            Rule(r"^(?:(?:включ\w+|поставь|запусти)\s+)?(?:избранн\w+|любим\w+)(?:\s+(?:треки|музыку|песни))?$",
                 lambda m: self.play_favorites()),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("favorite_current", "Добавить текущий играющий трек в избранное",
                 lambda a: self.favorite_current()),
            Tool("play_favorites", "Включить сохранённое избранное (только «включи избранное/"
                                   "любимое», НЕ для песен конкретного исполнителя)",
                 lambda a: self.play_favorites()),
        ]
