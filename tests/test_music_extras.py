"""Музыкальные мелочи: «включи что-нибудь», очередь, повтор, перемешивание."""

import random

import pytest

from src.core.agent import Agent
from src.skills.music import MusicSkill
from src.skills.playback import PlaybackSkill
from src.skills.youtube import YoutubeSkill
from tests.fakes import FakeBrain, FakeYoutubeSearch, make_player

TRACK = {"title": "Song", "artists": [{"name": "Band"}]}
FAVORITES = [{"ref": {"title": f"Fav {i}", "artists": []}, "addedAtIso": "2026-01-01"}
             for i in range(5)]


def make(canned=None):
    player, mcp = make_player(canned or {})
    music = MusicSkill(player, YoutubeSkill(player, FakeYoutubeSearch()))
    agent = Agent([PlaybackSkill(player), music], FakeBrain(content="ответ модели"))
    return agent, mcp


def test_play_something_shuffles_favorites(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: seq.reverse())  # предсказуемо
    agent, mcp = make({"Favorites.getTracks": FAVORITES})
    assert agent.handle("включи что-нибудь") == "Включаю избранное вперемешку: 5 треков"
    queued = mcp.method_calls("Queue.addToQueue")[0]["tracks"]
    assert [t["title"] for t in queued] == [f"Fav {i}" for i in reversed(range(5))]
    assert mcp.method_calls("Queue.clearQueue")  # очередь заменяется целиком


def test_play_something_falls_back_to_playlist():
    canned = {
        "Favorites.getTracks": [],
        "Playlists.getIndex": [{"id": "p1", "name": "Дорога"}],
        "Playlists.getPlaylist": {"items": [{"track": TRACK}]},
    }
    agent, _ = make(canned)
    assert agent.handle("поставь что-нибудь") == "Включаю плейлист «Дорога» вперемешку: 1 треков"


def test_play_something_with_nothing():
    agent, _ = make({"Favorites.getTracks": [], "Playlists.getIndex": []})
    assert agent.handle("включи что-нибудь") == "Нечего включить: ни избранного, ни плейлистов"


@pytest.mark.parametrize("command", [
    "добавь в очередь nirvana",
    "добавь nirvana в очередь",
    "закинь в очередь nirvana",
])
def test_add_to_queue_does_not_clear(command):
    agent, mcp = make({"Metadata.search": {"tracks": [TRACK]}})
    assert agent.handle(command) == "Добавил в очередь: Band — Song"
    assert mcp.method_calls("Queue.addToQueue")[0]["tracks"] == [TRACK]
    assert not mcp.method_calls("Queue.clearQueue")  # текущий трек не прерываем
    assert not mcp.method_calls("Playback.play")


def test_add_to_queue_not_found():
    agent, mcp = make({"Metadata.search": {"tracks": []}})
    assert agent.handle("добавь в очередь абракадабра") == "Не нашёл «абракадабра»"
    assert not mcp.method_calls("Queue.addToQueue")


@pytest.mark.parametrize("command, mode, phrase", [
    ("включи повтор", "all", "Повторяю очередь"),
    ("повтор", "all", "Повторяю очередь"),
    ("повтор трека", "one", "Повторяю трек"),
    ("повтори трек", "one", "Повторяю трек"),
    ("зацикли этот трек", "one", "Повторяю трек"),
    ("выключи повтор", "off", "Повтор выключен"),
])
def test_repeat_modes(command, mode, phrase):
    agent, mcp = make()
    assert agent.handle(command) == phrase
    assert mcp.method_calls("Playback.setRepeatMode") == [{"mode": mode}]


@pytest.mark.parametrize("command, enabled, phrase", [
    ("перемешай", True, "Перемешиваю"),
    ("включи перемешивание", True, "Перемешиваю"),
    ("вперемешку", True, "Перемешиваю"),
    ("по порядку", False, "Играю по порядку"),
    ("выключи перемешивание", False, "Играю по порядку"),
])
def test_shuffle(command, enabled, phrase):
    agent, mcp = make()
    assert agent.handle(command) == phrase
    assert mcp.method_calls("Playback.setShuffleEnabled") == [{"enabled": enabled}]


def test_existing_commands_still_work():
    """Новые правила не должны перехватывать старые команды."""
    agent, mcp = make({"Metadata.search": {"albums": [{"source": {"id": "a1"}, "title": "X"}]},
                       "Metadata.fetchAlbumDetails": {"title": "X", "tracks": [TRACK]}})
    assert agent.handle("включи альбом nevermind").startswith("Включаю альбом")
    assert agent.handle("пауза") == "Пауза"
    assert agent.handle("дальше") == "Следующий трек"
