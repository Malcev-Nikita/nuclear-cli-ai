"""Контекст последней команды: «сколько я отработал вчера» -> «а за позавчера?»."""

from datetime import date

import src.core.agent as agent_module
from src.core.agent import Agent
from src.skills.notes import NotesSkill
from src.skills.weather import WeatherSkill
from src.skills.worktime import WorktimeSkill
from tests.fakes import FakeBrain, FakeWeather
from tests.test_worktime import FakeB24, entry


def test_worktime_follow_up_changes_period():
    b24 = FakeB24([entry(1, 3600)])
    agent = Agent([WorktimeSkill(b24)], FakeBrain(content="ответ модели"))
    agent.handle("сколько я отработал вчера")
    yesterday = b24.calls[-1]
    answer = agent.handle("а за позавчера")
    assert answer.startswith("Позавчера ты проработал")
    assert b24.calls[-1] != yesterday  # спросили другой день


def test_follow_up_needs_context():
    """Без предыдущей команды «а за позавчера» — не наше дело, уходит в LLM."""
    agent = Agent([WorktimeSkill(FakeB24())], FakeBrain(content="ответ модели"))
    assert agent.handle("а за позавчера") == "ответ модели"


def test_follow_up_unknown_tail_goes_to_llm():
    agent = Agent([WorktimeSkill(FakeB24([entry(1, 3600)]))], FakeBrain(content="ответ модели"))
    agent.handle("сколько я отработал вчера")
    assert agent.handle("а включи нирвану") == "ответ модели"


def test_follow_up_expires(monkeypatch):
    """Через три минуты это уже другой разговор — уточнение не применяем."""
    now = [1000.0]
    monkeypatch.setattr(agent_module.time, "monotonic", lambda: now[0])
    agent = Agent([WorktimeSkill(FakeB24([entry(1, 3600)]))], FakeBrain(content="ответ модели"))
    agent.handle("сколько я отработал вчера")
    now[0] += agent_module.FOLLOW_UP_MEMORY_SEC + 1
    assert agent.handle("а за позавчера") == "ответ модели"


def test_weather_follow_up_city_and_day():
    agent = Agent([WeatherSkill(FakeWeather())], FakeBrain(content="ответ модели"))
    agent.handle("какая погода")
    assert agent.handle("а в москве") == "Сейчас в москве плюс 20, ясно"
    assert agent.handle("а завтра") == "Завтра в городе дождь"


def test_notes_follow_up_searches(tmp_path):
    skill = NotesSkill(str(tmp_path))
    agent = Agent([skill], FakeBrain(content="ответ модели"))
    agent.handle("запиши купить хостинг")
    agent.handle("запиши позвонить маме")
    answer = agent.handle("а про хостинг")
    assert "купить хостинг" in answer and "позвонить маме" not in answer


def test_context_switches_between_skills(tmp_path):
    """Последним отвечал другой навык — уточнение уходит ему, а не прежнему."""
    agent = Agent([WorktimeSkill(FakeB24([entry(1, 3600)])), WeatherSkill(FakeWeather())],
                  FakeBrain(content="ответ модели"))
    agent.handle("сколько я отработал вчера")
    agent.handle("какая погода")
    assert agent.handle("а в казани") == "Сейчас в казани плюс 20, ясно"
