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
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("save_note", "Сохранить заметку или напоминание («запиши…», «запомни…»)",
                 lambda a: self.save(a.get("text", "")),
                 params={"text": {"type": "string", "description": "Текст заметки дословно"}},
                 required=["text"], query_arg="text"),
            Tool("read_notes", "Прочитать последние сохранённые заметки",
                 lambda a: self.read_last()),
        ]
