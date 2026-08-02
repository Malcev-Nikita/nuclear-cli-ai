"""Таймеры, будильники и напоминания: список с временем срабатывания.

Хранится в файле — будильник на утро не должен пропадать оттого, что вечером
закрыли консоль. Никаких потоков: сработавшие забирает голосовой цикл, когда
микрофон молчит (см. MicSegmenter.utterances(idle_tick)).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

STALE_HOURS = 12  # пропущенное сильнее — уже неактуально, молчим


@dataclass
class Reminder:
    at: float    # unix-время срабатывания
    text: str    # «выключить духовку»; у голого таймера пусто
    kind: str    # таймер | будильник | напоминание

    def when(self) -> datetime:
        return datetime.fromtimestamp(self.at)


class Reminders:
    def __init__(self, path: str):
        self._path = path
        self._items: list[Reminder] = self._load()

    def add(self, at: datetime, text: str = "", kind: str = "напоминание") -> Reminder:
        item = Reminder(at.timestamp(), text.strip(), kind)
        self._items.append(item)
        self._items.sort(key=lambda r: r.at)
        self._save()
        return item

    def pending(self) -> list[Reminder]:
        return list(self._items)

    def due(self, now: datetime | None = None) -> list[Reminder]:
        """Забрать сработавшие (из списка они удаляются)."""
        moment = (now or datetime.now()).timestamp()
        fired = [r for r in self._items if r.at <= moment]
        if not fired:
            return []
        self._items = [r for r in self._items if r.at > moment]
        self._save()
        stale = moment - STALE_HOURS * 3600
        return [r for r in fired if r.at >= stale]

    def cancel(self, kind: str = "") -> list[Reminder]:
        """Снять всё (пустой kind) или только один вид. -> что сняли."""
        removed = [r for r in self._items if not kind or r.kind == kind]
        if removed:
            self._items = [r for r in self._items if r not in removed]
            self._save()
        return removed

    # -- файл

    def _load(self) -> list[Reminder]:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        items = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                items.append(Reminder(float(entry["at"]), str(entry.get("text", "")),
                                      str(entry.get("kind", "напоминание"))))
            except (KeyError, TypeError, ValueError):
                continue  # битую запись просто пропускаем
        return sorted(items, key=lambda r: r.at)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in self._items], f, ensure_ascii=False, indent=1)
