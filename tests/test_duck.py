"""Приглушение музыки на время речи: громкость возвращается всегда."""

import pytest

from src.audio.duck import Ducker
from tests.fakes import make_player

PLAYING = {"Playback.getState": {"status": "playing"}, "Playback.getVolume": 80}
STOPPED = {"Playback.getState": {"status": "stopped"}, "Playback.getVolume": 80}


def volumes(mcp) -> list:
    return [p["volume"] for p in mcp.method_calls("Playback.setVolume")]


def test_ducks_and_restores():
    player, mcp = make_player(PLAYING)
    with Ducker(player, 25).quiet():
        assert volumes(mcp) == [25]  # приглушили на время речи
    assert volumes(mcp) == [25, 80]  # и вернули как было


def test_restores_even_on_error():
    player, mcp = make_player(PLAYING)
    with pytest.raises(RuntimeError):
        with Ducker(player, 25).quiet():
            raise RuntimeError("озвучка упала")
    assert volumes(mcp) == [25, 80]


def test_silence_is_not_touched():
    """Музыка не играет — громкость вообще не наша забота."""
    player, mcp = make_player(STOPPED)
    with Ducker(player, 25).quiet():
        pass
    assert volumes(mcp) == []


def test_already_quiet_is_not_touched():
    player, mcp = make_player({**PLAYING, "Playback.getVolume": 15})
    with Ducker(player, 25).quiet():
        pass
    assert volumes(mcp) == []  # 15% и так тише 25%


def test_disabled_by_zero():
    player, mcp = make_player(PLAYING)
    with Ducker(player, 0).quiet():
        pass
    assert mcp.calls == []  # ни одного запроса к Nuclear


def test_survives_dead_nuclear():
    """Nuclear закрыли — ассистент всё равно должен договорить."""

    class Broken:
        def state(self):
            raise ConnectionError("нет связи")

        def volume_pct(self):
            raise ConnectionError("нет связи")

        def set_volume_pct(self, level):
            raise ConnectionError("нет связи")

    spoken = []
    with Ducker(Broken(), 25).quiet():
        spoken.append("сказал")
    assert spoken == ["сказал"]


def test_unit_scale_volume():
    """Шкала 0-1: приглушение считается в тех же единицах, что у Nuclear."""
    player, mcp = make_player({"Playback.getState": {"status": "playing"},
                               "Playback.getVolume": 0.8})
    with Ducker(player, 25).quiet():
        pass
    assert volumes(mcp) == [0.25, 0.8]
