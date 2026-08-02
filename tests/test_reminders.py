"""Таймеры, будильники, напоминания: разбор фраз, хранение, срабатывание."""

import json
from datetime import datetime, timedelta

import pytest

from src.core.agent import Agent
from src.services.reminders import Reminders
from src.skills.reminders import (
    RemindersSkill, announce, parse_delay, split_clock, split_delay,
)
from tests.fakes import FakeBrain

NOON = datetime(2026, 8, 2, 12, 0, 0)


def make(tmp_path):
    store = Reminders(str(tmp_path / "reminders.json"))
    skill = RemindersSkill(store)
    return skill, store, Agent([skill], FakeBrain(content="ответ модели"))


# -- разбор сроков


@pytest.mark.parametrize("text, seconds", [
    ("10 минут", 600),
    ("30 секунд", 30),
    ("2 часа", 7200),
    ("полтора часа", 5400),
    ("полчаса", 1800),
    ("час 30 минут", 5400),
    ("пять минут", 300),
    ("час", 3600),
    ("минуту", 60),
    ("абракадабра", None),
])
def test_parse_delay(text, seconds):
    assert parse_delay(text) == seconds


def test_split_delay_keeps_task_text():
    assert split_delay("через час выключить духовку") == (3600, "выключить духовку")
    assert split_delay("через 10 минут позвонить маме") == (600, "позвонить маме")
    assert split_delay("завтра позвонить") is None


@pytest.mark.parametrize("text, hour, minute, rest", [
    ("в 7 утра", 7, 0, ""),
    ("в 19:30", 19, 30, ""),
    ("в 7 вечера", 19, 0, ""),
    ("в 6:30 позвонить маме", 6, 30, "позвонить маме"),
])
def test_split_clock(text, hour, minute, rest):
    at, tail = split_clock(text, NOON)
    assert (at.hour, at.minute) == (hour, minute)
    assert tail == rest


def test_split_clock_past_time_moves_to_tomorrow():
    at, _ = split_clock("в 7 утра", NOON)  # сейчас полдень — значит, завтра
    assert at.date() == (NOON + timedelta(days=1)).date()
    at, _ = split_clock("в 15:00", NOON)  # ещё сегодня
    assert at.date() == NOON.date()


def test_split_clock_tomorrow_word():
    at, tail = split_clock("завтра в 15 00 забрать посылку", NOON)
    assert at.date() == (NOON + timedelta(days=1)).date()
    assert tail == "забрать посылку"


# -- постановка через роутер


def test_timer(tmp_path):
    skill, store, agent = make(tmp_path)
    assert agent.handle("поставь таймер на 10 минут") == "Поставил таймер на 10 минут"
    assert len(store.pending()) == 1
    assert store.pending()[0].kind == "таймер"


def test_timer_with_label(tmp_path):
    _, _, agent = make(tmp_path)
    assert agent.handle("таймер на 5 минут чайник") == "Поставил таймер на 5 минут — чайник"


def test_reminder_by_delay(tmp_path):
    _, store, agent = make(tmp_path)
    assert agent.handle("напомни через час выключить духовку") == \
        "Напомню через час: выключить духовку"
    assert store.pending()[0].text == "выключить духовку"


def test_reminder_by_clock(tmp_path):
    _, store, agent = make(tmp_path)
    answer = agent.handle("напомни в 23:30 поставить стирку")
    assert answer.startswith("Напомню в 23 часа 30 минут")
    assert store.pending()[0].kind == "напоминание"


def test_reminder_without_text(tmp_path):
    _, _, agent = make(tmp_path)
    assert agent.handle("напомни через час") == "А о чём напомнить?"


def test_alarm(tmp_path):
    _, store, agent = make(tmp_path)
    assert agent.handle("разбуди в 7 утра") == "Разбужу в 7 часов"
    assert store.pending()[0].kind == "будильник"
    assert agent.handle("поставь будильник на 6:30") == "Разбужу в 6 часов 30 минут"


def test_unparsable(tmp_path):
    _, _, agent = make(tmp_path)
    assert agent.handle("поставь таймер на потом") == "Не понял, на сколько ставить таймер"


# -- просмотр, отмена, срабатывание


def test_list_and_cancel(tmp_path):
    _, store, agent = make(tmp_path)
    agent.handle("таймер на 10 минут")
    agent.handle("разбуди в 7 утра")
    listing = agent.handle("какие таймеры")
    assert listing.startswith("2 штуки") and "будильник на 7 часов" in listing
    assert agent.handle("отмени таймер") == "Отменил таймер"
    assert len(store.pending()) == 1
    assert agent.handle("отмени всё") == "Отменил будильник"
    assert agent.handle("отмени всё") == "Нечего отменять"
    assert agent.handle("какие таймеры") == "Ничего не поставлено"


def test_due_fires_once(tmp_path):
    skill, store, _ = make(tmp_path)
    store.add(datetime.now() - timedelta(seconds=1), "чайник", "таймер")
    store.add(datetime.now() + timedelta(hours=1), "потом", "таймер")
    fired = store.due()
    assert [r.text for r in fired] == ["чайник"]
    assert store.due() == []  # второй раз не повторяется
    assert len(store.pending()) == 1


def test_stale_reminders_are_dropped(tmp_path):
    _, store, _ = make(tmp_path)
    store.add(datetime.now() - timedelta(hours=20), "вчерашнее", "напоминание")
    assert store.due() == []  # проспали больше 12 часов — не выкрикиваем
    assert store.pending() == []  # но и в списке не держим


def test_survives_restart(tmp_path):
    _, store, agent = make(tmp_path)
    agent.handle("разбуди в 7 утра")
    reopened = Reminders(str(tmp_path / "reminders.json"))
    assert len(reopened.pending()) == 1
    assert reopened.pending()[0].kind == "будильник"


def test_broken_file_does_not_crash(tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text("{ это не json", encoding="utf-8")
    assert Reminders(str(path)).pending() == []
    path.write_text(json.dumps([{"нет": "полей"}]), encoding="utf-8")
    assert Reminders(str(path)).pending() == []


def test_announce_phrases(tmp_path):
    _, store, _ = make(tmp_path)
    timer = store.add(datetime.now(), "чайник", "таймер")
    assert announce(timer) == "Таймер, чайник"
    bare = store.add(datetime.now(), "", "таймер")
    assert announce(bare) == "Таймер! Время вышло"
    alarm = store.add(datetime(2026, 8, 3, 7, 0), "", "будильник")
    assert announce(alarm) == "Подъём! 7 часов"
    note = store.add(datetime.now(), "выключить духовку", "напоминание")
    assert announce(note) == "Напоминаю: выключить духовку"


def test_follow_up_adds_another_timer(tmp_path):
    _, store, agent = make(tmp_path)
    agent.handle("таймер на 10 минут")
    assert agent.handle("а ещё на 5 минут") == "Поставил таймер на 5 минут"
    assert len(store.pending()) == 2
