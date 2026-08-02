"""Погода: сейчас и прогноз на будущие дни (Open-Meteo через сервис)."""

from __future__ import annotations

import re

from src.services.weather import OpenMeteoWeather, parse_when
from src.skills.base import Rule, Skill, Tool

# «в \w+» тут нельзя: «погода в москве» — это город, а не день недели
_WEEKDAY = r"в[ов]?\s+(?:понедельник|вторник|сред\w+|четверг|пятниц\w+|суббот\w+|воскресен\w+)"
_WHEN = rf"(сегодня|завтра|послезавтра|на\s+выходны\w+|в\s+выходные|{_WEEKDAY})"


class WeatherSkill(Skill):
    def __init__(self, weather: OpenMeteoWeather):
        self.weather = weather

    def rules(self) -> list[Rule]:
        return [
            # прогноз — раньше «сейчас», иначе «погода завтра» съест правило текущей
            Rule(rf"^(?:какая\s+)?(?:будет\s+)?погода\s+(?:будет\s+)?{_WHEN}"
                 r"(?:\s+в\s+(.+?))?\s*\??$",
                 lambda m: self.weather.forecast(m.group(2) or "", m.group(1))),
            Rule(rf"^что\s+(?:там\s+)?{_WHEN}\s+(?:с\s+)?погод\w*\s*\??$",
                 lambda m: self.weather.forecast("", m.group(1))),
            Rule(rf"^будет\s+ли\s+(?:{_WHEN}\s+)?(дождь|снег)(?:\s+{_WHEN})?\s*\??$",
                 lambda m: self.weather.will_precipitate(
                     "", m.group(1) or m.group(3) or "завтра", m.group(2))),
            Rule(r"^(?:какая\s+)?(?:сейчас\s+)?погода(?:\s+(?:сейчас|сегодня))?"
                 r"(?:\s+(?:в|на)\s+(.+?))?\s*\??$",
                 lambda m: self.weather.get(m.group(1) or "")),
            Rule(r"^сколько (?:сейчас )?градусов(?: на улице)?\??$",
                 lambda m: self.weather.get("")),
        ]

    def follow_up(self, text: str) -> str | None:
        """«а завтра?» -> прогноз; «а в Москве?» -> та же погода, другой город."""
        when = parse_when(text)
        if when:
            return self.weather.forecast("", text)
        city = re.match(r"^(?:в|во|на)\s+(.+)$", text.strip())
        if city:
            return self.weather.get(city.group(1))
        if len(text.split()) == 1 and len(text) > 2 and text.isalpha():
            return self.weather.get(text)  # одно слово после «а» — считаем городом
        return None

    def tools(self) -> list[Tool]:
        city_param = {"type": "string",
                      "description": "Город, если назван; иначе пустая строка"}
        return [
            Tool("get_weather", "Узнать погоду сейчас и на сегодня",
                 lambda a: self.weather.get(a.get("city") or ""),
                 params={"city": city_param}, query_arg="city"),
            Tool("get_forecast", "Прогноз погоды на завтра, послезавтра, выходные или день недели",
                 lambda a: self.weather.forecast(a.get("city") or "", a.get("when") or "завтра"),
                 params={"when": {"type": "string",
                                  "description": "«завтра», «послезавтра», «на выходных», «в пятницу»"},
                         "city": city_param},
                 required=["when"]),
        ]
