"""Прогноз погоды: разбор «когда», фразы, роутер."""

from datetime import date

import pytest

from src.core.agent import Agent
from src.services.weather import OpenMeteoWeather, _dealias, parse_when
from src.skills.weather import WeatherSkill
from tests.fakes import FakeBrain, FakeWeather

SUNDAY = date(2026, 8, 2)  # воскресенье

DAYS = [  # сегодня + 8 дней вперёд
    {"code": 0, "high": 30, "low": 20, "rain": 0},     # 0 вс — ясно
    {"code": 63, "high": 25, "low": 18, "rain": 70},   # 1 пн — дождь
    {"code": 3, "high": 27, "low": 19, "rain": 10},    # 2 вт — пасмурно
    {"code": 0, "high": 28, "low": 20, "rain": 0},     # 3 ср
    {"code": 71, "high": 2, "low": -5, "rain": 60},    # 4 чт — снег
    {"code": 0, "high": 29, "low": 21, "rain": 0},     # 5 пт
    {"code": 95, "high": 24, "low": 18, "rain": 90},   # 6 сб — гроза
    {"code": 0, "high": 26, "low": 19, "rain": 0},     # 7 вс
    {"code": 0, "high": 26, "low": 19, "rain": 0},     # 8 пн
]


class StubMeteo(OpenMeteoWeather):
    """Подменяет только поход в сеть — разбор и фразы настоящие."""

    def _daily(self, city=""):
        return (city or "Астрахань"), DAYS


def test_parse_when_relative():
    assert parse_when("завтра", SUNDAY) == ([1], "завтра")
    assert parse_when("послезавтра", SUNDAY) == ([2], "послезавтра")
    assert parse_when("сегодня", SUNDAY) == ([0], "сегодня")
    assert parse_when("", SUNDAY) is None
    assert parse_when("на хлебе", SUNDAY) is None


def test_parse_when_weekend_and_weekdays():
    # воскресенье 2 августа: ближайшая суббота через 6 дней
    assert parse_when("на выходных", SUNDAY) == ([6, 7], "на выходных")
    assert parse_when("в пятницу", SUNDAY) == ([5], "в пятницу")
    assert parse_when("в среду", SUNDAY) == ([3], "в среду")
    # «в воскресенье» в воскресенье — это через неделю, а не сегодня
    assert parse_when("в воскресенье", SUNDAY) == ([7], "в воскресенье")


def test_city_aliases():
    assert _dealias("питере") == "Санкт-Петербург"
    assert _dealias("спб") == "Санкт-Петербург"
    assert _dealias("нижний тагил") == "нижний тагил"  # не путать с Новгородом
    assert _dealias("казани") == "казани"


def test_forecast_tomorrow():
    answer = StubMeteo().forecast("Астрахань", "завтра", today=SUNDAY)
    assert answer == ("Завтра в городе Астрахань дождь, от плюс 18 до плюс 25, "
                      "осадки с вероятностью 70 процентов")


def test_forecast_weekend_names_both_days():
    answer = StubMeteo().forecast("", "на выходных", today=SUNDAY)
    assert answer.startswith("На выходных")
    assert "в субботу" in answer and "в воскресенье" in answer
    assert "гроза" in answer  # суббота из DAYS


def test_forecast_unknown_when_falls_back_to_tomorrow():
    assert StubMeteo().forecast("", "когда-нибудь", today=SUNDAY).startswith("Завтра")


def test_will_precipitate():
    weather = StubMeteo()
    assert weather.will_precipitate("", "завтра", "дождь", today=SUNDAY) == \
        "Завтра обещают дождь, вероятность 70 процентов"
    assert weather.will_precipitate("", "послезавтра", "дождь", today=SUNDAY) == \
        "Послезавтра дождь не обещают"
    assert weather.will_precipitate("", "в четверг", "снег", today=SUNDAY) \
        .startswith("В четверг обещают снег")
    assert weather.will_precipitate("", "завтра", "снег", today=SUNDAY) == "Завтра снег не обещают"


@pytest.mark.parametrize("command, expect", [
    ("погода завтра", "Завтра в городе дождь"),
    ("какая погода будет завтра", "Завтра в городе дождь"),
    # вживую 2026-08-02: «на завтра» уезжало в название города
    ("погода на завтра", "На завтра в городе дождь"),
    ("погоды на завтра в астрахани", "На завтра в астрахани дождь"),
    ("прогноз на завтра", "На завтра в городе дождь"),
    ("погода на послезавтра", "На послезавтра в городе дождь"),
    ("погода на выходных", "На выходных в городе дождь"),
    ("что завтра с погодой", "Завтра в городе дождь"),
    ("будет ли завтра дождь", "Завтра обещают дождь"),
    ("будет ли дождь в пятницу", "В пятницу обещают дождь"),
    # текущая погода не должна уехать в прогноз
    ("какая погода", "Сейчас в городе плюс 20, ясно"),
    ("погода в казани", "Сейчас в казани плюс 20, ясно"),
])
def test_router_forecast_vs_current(command, expect):
    agent = Agent([WeatherSkill(FakeWeather())], FakeBrain(content="ответ модели"))
    assert agent.handle(command) == expect
