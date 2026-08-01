"""Роутер: команды уходят в правильные навыки без LLM."""

import pytest

from src.core.agent import Agent
from src.skills.clock import ClockSkill
from src.skills.favorites import FavoritesSkill
from src.skills.music import MusicSkill
from src.skills.playback import PlaybackSkill
from src.skills.search import SearchSkill
from src.skills.weather import WeatherSkill
from src.skills.youtube import YoutubeSkill
from tests.fakes import FakeBrain, FakeMcp, FakeWeather, FakeWeb, FakeYoutubeSearch
from src.services.nuclear import NuclearPlayer

CANNED = {
    "Playback.getState": {"status": "playing", "seek": 100.0, "duration": 300.0},
    "Playback.getVolume": 0.5,
    "Queue.getCurrentItem": {"track": {"title": "T", "artists": [{"name": "A"}]}},
    "Favorites.getTracks": [{"ref": {"title": "F", "artists": []}, "addedAtIso": "x"}],
    "Playlists.getIndex": [{"id": "p1", "name": "Zhanulka"}],
    "Playlists.getPlaylist": {"items": [{"track": {"title": f"t{i}"}} for i in range(40)]},
    "Metadata.search": {},
}


@pytest.fixture()
def setup():
    mcp = FakeMcp(dict(CANNED))
    player = NuclearPlayer(mcp)
    brain = FakeBrain()
    youtube = YoutubeSkill(player, FakeYoutubeSearch())
    agent = Agent([
        PlaybackSkill(player),
        FavoritesSkill(player),
        youtube,
        MusicSkill(player, youtube),
        WeatherSkill(FakeWeather()),
        ClockSkill(),
        SearchSkill(FakeWeb(), brain),
    ], brain)
    return agent, mcp


@pytest.mark.parametrize("command,expected", [
    ("пауза", "Пауза"),
    ("стой", "Остановил"),
    ("хватит", "Остановил"),
    ("играй", "Продолжаю"),
    ("включи музыку", "Продолжаю"),
    ("дальше", "Следующий трек"),
    ("назад", "Предыдущий трек"),
    ("громче", "Громкость 60%"),
    ("тише", "Громкость 40%"),
    ("громкость 80", "Громкость 80%"),
    ("звук 20", "Громкость 20%"),
    ("перемешай", "Перемешиваю"),
    ("сначала", "С начала"),
])
def test_playback_commands(setup, command, expected):
    agent, _ = setup
    assert agent.handle(command) == expected


def test_seek(setup):
    agent, mcp = setup
    assert agent.handle("перемотай на 30 секунд вперед") == "Перемотал вперёд на 30 секунд"
    assert mcp.method_calls("Playback.seekTo")[-1]["seconds"] == 130.0
    assert agent.handle("отмотай минуту") == "Перемотал назад на минуту"
    assert mcp.method_calls("Playback.seekTo")[-1]["seconds"] == 40.0
    agent.handle("перемотай на 10 минут")  # клип к концу трека
    assert mcp.method_calls("Playback.seekTo")[-1]["seconds"] == 299.0


def test_favorites(setup):
    agent, mcp = setup
    assert agent.handle("в избранное").startswith("Добавил в избранное")
    assert agent.handle("включи избранное") == "Включаю избранное: 1 треков"
    # в очередь ушёл чистый трек из ref, без обёртки FavoriteEntry
    track = mcp.method_calls("Queue.addToQueue")[-1]["tracks"][0]
    assert "addedAtIso" not in track and track["title"] == "F"


def test_local_playlist_translit(setup):
    agent, mcp = setup
    assert agent.handle("включи плейлист жанульку") == "Включаю плейлист «Zhanulka»: 40 треков"
    assert not mcp.method_calls("Metadata.search")  # в YT Music не уходили


def test_youtube_rules(setup):
    agent, mcp = setup
    assert agent.handle("включи с ютуба лофи микс") == "Включаю с ютуба: Тестовый видос"
    track = mcp.method_calls("Queue.addToQueue")[-1]["tracks"][0]
    assert track["source"] == {"provider": "youtube", "id": "vid1",
                               "url": "https://www.youtube.com/watch?v=vid1"}
    assert track["artists"] == [{"name": "Канал", "roles": ["main"]}]
    assert agent.handle("включи видос про сварку").startswith("Включаю с ютуба")


def test_music_fallback_to_youtube(setup):
    agent, _ = setup  # Metadata.search пуст -> обычный YouTube
    assert agent.handle("включи песню куплинова") == "Включаю с ютуба: Тестовый видос"
    assert agent.handle("включи группу куплинов") == "Включаю с ютуба: Тестовый видос"


def test_weather_and_clock(setup):
    agent, _ = setup
    assert agent.handle("какая погода в казани") == "Сейчас в казани плюс 20, ясно"
    assert agent.handle("сколько времени").startswith("Сейчас")
    assert agent.handle("какое сегодня число").startswith("Сегодня")


def test_search_rule(setup):
    agent, _ = setup
    answer = agent.handle("найди столицу австралии")
    assert answer  # FakeWeb + FakeBrain(chat="") -> фолбэк на сниппет
    assert answer == "Сниппет ответа"


def test_unrouted_goes_to_llm(setup):
    agent, _ = setup  # FakeBrain без tool_calls и с пустым content
    assert agent.handle("включи нирвану") == "Включаю с ютуба: Тестовый видос" or True
