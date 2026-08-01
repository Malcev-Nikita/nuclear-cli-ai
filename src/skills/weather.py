"""Погода (Open-Meteo через сервис)."""

from __future__ import annotations

from src.services.weather import OpenMeteoWeather
from src.skills.base import Rule, Skill, Tool


class WeatherSkill(Skill):
    def __init__(self, weather: OpenMeteoWeather):
        self.weather = weather

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:какая\s+)?(?:сейчас\s+)?погода(?:\s+(?:сейчас|сегодня))?(?:\s+(?:в|на)\s+(.+?))?\s*\??$",
                 lambda m: self.weather.get(m.group(1) or "")),
            Rule(r"^сколько (?:сейчас )?градусов(?: на улице)?\??$",
                 lambda m: self.weather.get("")),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("get_weather", "Узнать погоду сейчас и на сегодня",
                 lambda a: self.weather.get(a.get("city") or ""),
                 params={"city": {"type": "string",
                                  "description": "Город, если назван; иначе пустая строка"}},
                 query_arg="city"),
        ]
