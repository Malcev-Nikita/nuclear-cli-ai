"""Рабочее время B24: разбор периодов, ответы, роутер."""

from datetime import date

from src.core.agent import Agent
from src.skills.worktime import WorktimeSkill, parse_period, spoken_work
from tests.fakes import FakeBrain

TODAY = date(2026, 8, 2)  # воскресенье


class FakeB24:
    def __init__(self, items=None, titles=None):
        self.items = items or []
        self.titles = titles or {}
        self.calls = []

    def elapsed(self, frm, to):
        self.calls.append((frm, to))
        return self.items

    def task_titles(self, ids):
        return {i: self.titles[i] for i in ids if i in self.titles}


def entry(task_id, seconds, day="2026-08-02"):
    return {"TASK_ID": task_id, "SECONDS": seconds, "CREATED_DATE": f"{day}T12:00:00+03:00"}


def test_parse_period_relative_days():
    assert parse_period("сегодня", TODAY) == (TODAY, TODAY, "сегодня")
    assert parse_period("", TODAY) == (TODAY, TODAY, "сегодня")
    assert parse_period("вчера", TODAY) == (date(2026, 8, 1), date(2026, 8, 1), "вчера")
    assert parse_period("за позавчера", TODAY) == (date(2026, 7, 31), date(2026, 7, 31), "позавчера")


def test_parse_period_ranges():
    assert parse_period("за неделю", TODAY) == (date(2026, 7, 27), TODAY, "за неделю")  # с понедельника
    assert parse_period("за месяц", TODAY) == (date(2026, 8, 1), TODAY, "за месяц")
    assert parse_period("за год", TODAY) == (date(2026, 1, 1), TODAY, "за год")


def test_parse_period_named_months():
    assert parse_period("за июль", TODAY) == (date(2026, 7, 1), date(2026, 7, 31), "за июль")
    assert parse_period("в мае", TODAY) == (date(2026, 5, 1), date(2026, 5, 31), "за май")
    assert parse_period("за июнь 2025 года", TODAY) == \
        (date(2025, 6, 1), date(2025, 6, 30), "за июнь 2025 года")
    assert parse_period("за декабрь", TODAY) == (date(2026, 12, 1), date(2026, 12, 31), "за декабрь")


def test_parse_period_previous():
    assert parse_period("в прошлом месяце", TODAY) == \
        (date(2026, 7, 1), date(2026, 7, 31), "за прошлый месяц")
    assert parse_period("за прошлую неделю", TODAY) == \
        (date(2026, 7, 20), date(2026, 7, 26), "за прошлую неделю")
    assert parse_period("за прошлый год", TODAY) == \
        (date(2025, 1, 1), date(2025, 12, 31), "за прошлый год")


def test_parse_period_dates():
    assert parse_period("за 26 июня", TODAY) == (date(2026, 6, 26), date(2026, 6, 26), "за 26 июня")
    assert parse_period("26-го июня", TODAY)[0] == date(2026, 6, 26)
    # «года» в дате не должно проваливаться в ветку «за год»
    assert parse_period("за 26 июня 2025 года", TODAY)[0] == date(2025, 6, 26)
    assert parse_period("за 5 мая", TODAY)[0] == date(2026, 5, 5)
    assert parse_period("31 февраля", TODAY) is None  # явная дата, но кривая
    # период не назван («сколько я отработал») — по умолчанию сегодня
    assert parse_period("сколько я отработал", TODAY) == (TODAY, TODAY, "сегодня")


def test_spoken_work():
    assert spoken_work(3 * 3600) == "3 часа"
    assert spoken_work(3900) == "1 час 5 минут"
    assert spoken_work(30 * 60) == "30 минут"
    assert spoken_work(10) == "меньше минуты"


def test_report_today_single_task():
    skill = WorktimeSkill(FakeB24([entry(7, 3600), entry(7, 1800)]))
    assert skill.report("сегодня") == "Сегодня ты проработал 1 час 30 минут"


def test_report_top_task_and_average():
    items = [entry(1, 3600, "2026-07-27"), entry(2, 7200, "2026-07-28"), entry(2, 3600, "2026-07-29")]
    skill = WorktimeSkill(FakeB24(items, titles={2: "Правки сайта"}))
    answer = skill.report("за неделю")
    assert answer.startswith("За неделю ты проработал 4 часа")
    assert "3 рабочих дня" in answer and "в среднем 1 час 20 минут" in answer
    assert "«Правки сайта» — 3 часа" in answer


def test_report_empty():
    assert WorktimeSkill(FakeB24()).report("вчера") == "Вчера записей времени нет"


def test_router_matches():
    skill = WorktimeSkill(FakeB24([entry(1, 3600)]))
    agent = Agent([skill], FakeBrain(content=""))
    assert "проработал" in agent.handle("сколько я сегодня проработал")
    assert "проработал" in agent.handle("сколько я отработал за неделю")
    assert "проработал" in agent.handle("сколько часов я работал вчера")


def test_worktime_tool_in_system_prompt():
    agent = Agent([WorktimeSkill(FakeB24())], FakeBrain(content=""))
    assert "work_time" in agent.system
    assert "work_time" not in Agent([], FakeBrain(content="")).system
