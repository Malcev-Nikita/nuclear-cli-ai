"""Заметки голосом: «запиши/запомни …» — текст в файл, «прочитай заметки» — назад."""

from __future__ import annotations

import os
import re
from datetime import datetime

from src.config import NOTES_DIR
from src.core.texts import plural
from src.skills.base import Rule, Skill, Tool


class NotesSkill(Skill):
    def __init__(self, notes_dir: str = NOTES_DIR):
        self._dir = notes_dir

    def _files(self) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        # имя начинается с даты-времени (с микросекундами) -> сортировка по
        # имени = хронология, даже для двух заметок в одну секунду
        return sorted(
            os.path.join(self._dir, name)
            for name in os.listdir(self._dir) if name.endswith(".txt")
        )

    def save(self, text: str) -> str:
        text = " ".join((text or "").split())
        if not text:
            return "Что записать?"
        os.makedirs(self._dir, exist_ok=True)
        slug = re.sub(r'[<>:"/\\|?*.]+', "", text)[:40].strip() or "заметка"
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S-%f")
        path = os.path.join(self._dir, f"{stamp} {slug}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return f"Записал: {text}"

    def read_last(self, count: int = 3) -> str:
        files = self._files()
        if not files:
            return "Заметок пока нет"
        notes = []
        for path in reversed(files[-count:]):
            with open(path, encoding="utf-8") as f:
                notes.append(f.read().strip())
        total = plural(len(files), "заметка", "заметки", "заметок")
        return f"Всего {len(files)} {total}. " + " ".join(f"«{note}»." for note in notes)

    def search(self, query: str) -> str:
        query = " ".join((query or "").split()).lower().replace("ё", "е")
        if not query:
            return "Что найти в заметках?"
        # слова короче 3 букв («о», «на») для поиска бесполезны
        words = [w for w in re.split(r"\W+", query) if len(w) > 2] or [query]
        found = []
        for path in reversed(self._files()):
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
            if all(word in text.lower().replace("ё", "е") for word in words):
                found.append(text)
        if not found:
            return f"Про «{query}» ничего не записано"
        head = found[:3]
        answer = f"Нашёл {len(found)} {plural(len(found), 'заметку', 'заметки', 'заметок')}: "
        return answer + " ".join(f"«{note}»." for note in head)

    def delete_last(self) -> str:
        files = self._files()
        if not files:
            return "Заметок пока нет"
        with open(files[-1], encoding="utf-8") as f:
            text = f.read().strip()
        os.remove(files[-1])
        return f"Удалил заметку: {text}"

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:запиши|запомни|сделай заметку)[,:]?\s+(.+)$",
                 lambda m: self.save(m.group(1))),
            Rule(r"^(?:прочитай|прочти|покажи|какие)\s+(?:мои\s+)?(?:все\s+)?заметки$",
                 lambda m: self.read_last()),
            Rule(r"^(?:удали|сотри)\s+последнюю\s+заметку$",
                 lambda m: self.delete_last()),
            Rule(r"^(?:что\s+я\s+(?:записывал|писал)|найди\s+в\s+заметках|"
                 r"поищи\s+в\s+заметках|покажи\s+заметки)\s*(?:про|о|об)?\s+(.+?)\s*\??$",
                 lambda m: self.search(m.group(1))),
        ]

    def follow_up(self, text: str) -> str | None:
        """«а про хостинг?» сразу после разговора о заметках — поиск."""
        return self.search(re.sub(r"^(?:про|о|об)\s+", "", text.strip()))

    def tools(self) -> list[Tool]:
        return [
            Tool("save_note", "Сохранить заметку или напоминание («запиши…», «запомни…»)",
                 lambda a: self.save(a.get("text", "")),
                 params={"text": {"type": "string", "description": "Текст заметки дословно"}},
                 required=["text"], query_arg="text"),
            Tool("read_notes", "Прочитать последние сохранённые заметки",
                 lambda a: self.read_last()),
            Tool("search_notes", "Найти среди заметок те, где упоминается слово",
                 lambda a: self.search(a.get("query", "")),
                 params={"query": {"type": "string", "description": "Что искать"}},
                 required=["query"], query_arg="query"),
        ]
