"""LLM-путь агента: tool calls, вырожденные ответы qwen3:1.7b, think-теги."""

from src.core.agent import Agent
from src.services.ollama import parse_text_tool_call, strip_think
from src.services.nuclear import NuclearPlayer
from src.skills.music import MusicSkill
from src.skills.playback import PlaybackSkill
from src.skills.youtube import YoutubeSkill
from tests.fakes import FakeBrain, FakeMcp, FakeYoutubeSearch


def make_agent(brain):
    player = NuclearPlayer(FakeMcp({"Metadata.search": {}}))
    youtube = YoutubeSkill(player, FakeYoutubeSearch())
    return Agent([PlaybackSkill(player), youtube, MusicSkill(player, youtube)], brain)


def test_strip_think():
    assert strip_think("<think>мысли</think>Ответ.") == "Ответ."
    # рассуждения БЕЗ открывающего тега (вживую 2026-08-01)
    assert strip_think("думаю...\n</think>\nОтвет.") == "Ответ."
    assert strip_think("Ответ <think>и зависшие мысли") == "Ответ"
    assert strip_think("Просто ответ.") == "Просто ответ."


def test_parse_text_tool_call():
    call = parse_text_tool_call('{"name": "pause", "arguments": {}}')
    assert call["function"]["name"] == "pause"
    call = parse_text_tool_call('```json\n{"name": "play_track", "arguments": {"query": "x"}}\n```')
    assert call["function"]["arguments"] == {"query": "x"}
    assert parse_text_tool_call("просто текст") is None
    assert parse_text_tool_call('{"foo": 1}') is None


def test_real_tool_call_executes():
    brain = FakeBrain(tool_calls=[{"function": {"name": "pause", "arguments": {}}}])
    assert make_agent(brain).handle("поставь на паузу") == "Пауза"


def test_bare_tool_name_fallback():
    # модель ответила голым именем инструмента (вживую 2026-08-01)
    agent = make_agent(FakeBrain(content="play_track"))
    assert agent.handle("включи куплинова") == "Включаю с ютуба: Тестовый видос"


def test_bare_noarg_tool_name():
    agent = make_agent(FakeBrain(content="pause"))
    assert agent.handle("поставь на паузу пожалуйста") == "Пауза"


def test_echo_fallback():
    # модель повторила команду эхом
    agent = make_agent(FakeBrain(content="включи куплиново"))
    assert agent.handle("включи куплиново") == "Включаю с ютуба: Тестовый видос"


def test_chitchat_passthrough():
    agent = make_agent(FakeBrain(content="Я не люблю кальян."))
    assert agent.handle("ты любишь кальян?") == "Я не люблю кальян."


def test_empty_content():
    agent = make_agent(FakeBrain(content=""))
    assert agent.handle("абракадабра зюзюка") == "Не понял команду"
