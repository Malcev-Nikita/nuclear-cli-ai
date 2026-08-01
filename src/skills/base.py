"""Каркас навыка: правила роутера + инструменты LLM в одном классе."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Rule:
    """Regex-правило быстрого роутера (срабатывает без LLM)."""
    pattern: str
    handler: Callable[[re.Match], str]


@dataclass
class Tool:
    """Инструмент для LLM. query_arg — имя строкового аргумента ("query"/"name"),
    через который агент чинит вырожденные ответы qwen3:1.7b."""
    name: str
    description: str
    impl: Callable[[dict], str]
    params: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    query_arg: str | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.params,
                    "required": self.required,
                },
            },
        }


class Skill:
    """Базовый класс: навык приносит свои правила и инструменты."""

    def rules(self) -> list[Rule]:
        return []

    def tools(self) -> list[Tool]:
        return []


QUERY_PARAM = {"type": "string", "description": "Название так, как сказал пользователь"}
