"""Заметки: запись в файлы, чтение, удаление, роутер и LLM-фолбэк."""

import os

from src.core.agent import Agent
from src.skills.notes import NotesSkill
from tests.fakes import FakeBrain


def make_agent(tmp_path, brain=None):
    return Agent([NotesSkill(str(tmp_path))], brain or FakeBrain(content=""))


def test_save_creates_file(tmp_path):
    answer = make_agent(tmp_path).handle("запиши купить хлеб")
    assert answer == "Записал: купить хлеб"
    files = os.listdir(tmp_path)
    assert len(files) == 1 and files[0].endswith(".txt")
    assert "купить хлеб" in files[0]
    content = (tmp_path / files[0]).read_text(encoding="utf-8")
    assert content.strip() == "купить хлеб"


def test_zapomni_and_read(tmp_path):
    agent = make_agent(tmp_path)
    agent.handle("запомни завтра к врачу")
    agent.handle("запиши позвонить маме")
    answer = agent.handle("прочитай заметки")
    # последняя — первой
    assert answer.index("позвонить маме") < answer.index("завтра к врачу")
    assert answer.startswith("Всего 2 заметки")


def test_read_empty(tmp_path):
    assert make_agent(tmp_path).handle("прочитай заметки") == "Заметок пока нет"


def test_delete_last(tmp_path):
    agent = make_agent(tmp_path)
    agent.handle("запиши первая")
    agent.handle("запиши вторая")
    assert agent.handle("удали последнюю заметку") == "Удалил заметку: вторая"
    assert len(os.listdir(tmp_path)) == 1


def test_save_via_llm_tool(tmp_path):
    brain = FakeBrain(tool_calls=[
        {"function": {"name": "save_note", "arguments": {"text": "полить цветы"}}}
    ])
    assert make_agent(tmp_path, brain).handle("надо бы не забыть полить цветы") \
        == "Записал: полить цветы"


def test_slug_strips_bad_chars(tmp_path):
    make_agent(tmp_path).handle('запиши цена: 10*2 "штук"')
    name = os.listdir(tmp_path)[0]
    assert not any(ch in name[len("2026-08-02 15-00-00"):] for ch in ':*?"')
