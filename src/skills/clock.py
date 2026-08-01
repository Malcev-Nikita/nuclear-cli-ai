"""Время и дата: локальные часы, ответы словами — piper так читает надёжнее."""

from __future__ import annotations

from datetime import datetime

from src.core.texts import plural
from src.skills.base import Rule, Skill, Tool

_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
               "августа", "сентября", "октября", "ноября", "декабря"]
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница",
             "суббота", "воскресенье"]


class ClockSkill(Skill):
    @staticmethod
    def get_time() -> str:
        now = datetime.now()
        hours = f"{now.hour} {plural(now.hour, 'час', 'часа', 'часов')}"
        if now.minute == 0:
            return f"Сейчас ровно {hours}"
        minutes = f"{now.minute} {plural(now.minute, 'минута', 'минуты', 'минут')}"
        return f"Сейчас {hours} {minutes}"

    @staticmethod
    def get_date() -> str:
        now = datetime.now()
        return f"Сегодня {now.day} {_MONTHS_GEN[now.month - 1]}, {_WEEKDAYS[now.weekday()]}"

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:а\s+)?(?:сколько\s+(?:сейчас\s+)?(?:времени|время)|который\s+час)(?:\s+на часах)?\s*\??$",
                 lambda m: self.get_time()),
            Rule(r"^(?:а\s+)?(?:какое\s+(?:сегодня\s+)?число|какой\s+(?:сегодня\s+)?день(?:\s+недели)?)\s*\??$",
                 lambda m: self.get_date()),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("get_time", "Сказать текущее время", lambda a: self.get_time()),
            Tool("get_date", "Сказать сегодняшнюю дату и день недели", lambda a: self.get_date()),
        ]
