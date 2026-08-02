"""Нарезка фраз и тик простоя (по нему голосовой цикл проверяет таймеры).

sounddevice в тестах нет — очередь блоков наполняем сами, минуя __init__.
"""

import itertools
import queue

import numpy as np
import pytest

import src.audio.mic as mic_module
from src.audio.mic import MicSegmenter
from src.config import BLOCK, SAMPLE_RATE, VAD_ABS_MIN


def silence(count):
    return [np.zeros((BLOCK, 1), dtype=np.float32) for _ in range(count)]


def speech(count):
    return [np.full((BLOCK, 1), 0.2, dtype=np.float32) for _ in range(count)]


def make_mic(blocks):
    mic = MicSegmenter.__new__(MicSegmenter)  # без звуковой карты
    mic._np = np
    mic._queue = queue.Queue()
    mic._noise = VAD_ABS_MIN
    for block in blocks:
        mic._queue.put(block)
    return mic


@pytest.fixture(autouse=True)
def no_lag_guard(monkeypatch):
    """Очередь набита заранее — иначе анти-лаг примет это за отставание и всё выбросит."""
    monkeypatch.setattr(mic_module, "MAX_LAG_SEC", 1000.0)


@pytest.fixture
def fake_clock(monkeypatch):
    """Время идёт на 0.2 с при каждом взгляде на часы."""
    ticks = itertools.count(0, 0.2)
    monkeypatch.setattr(mic_module.time, "monotonic", lambda: next(ticks))


def test_idle_tick_yields_none(fake_clock):
    mic = make_mic(silence(200))
    got = list(itertools.islice(mic.utterances(idle_tick=0.5), 3))
    assert got == [None, None, None]  # в тишине — только тики


def test_no_tick_without_idle_tick():
    """Без idle_tick генератор молчит до конца фразы (прежнее поведение)."""
    mic = make_mic(silence(50) + speech(30) + silence(20))
    first = next(mic.utterances())
    assert isinstance(first, np.ndarray)


def test_phrase_still_recognized_with_ticks(fake_clock):
    # хвост тишины большой: генератор блокируется, если блоки кончатся
    mic = make_mic(silence(10) + speech(30) + silence(200))
    got = list(itertools.islice(mic.utterances(idle_tick=0.5), 10))
    phrases = [item for item in got if item is not None]
    assert len(phrases) == 1
    assert len(phrases[0]) / SAMPLE_RATE > 0.8  # фраза целиком, а не огрызок


def test_no_tick_while_speaking(fake_clock):
    """Посреди фразы тик не выдаём — иначе таймер зазвонит поверх речи."""
    mic = make_mic(speech(60) + silence(200))
    got = list(itertools.islice(mic.utterances(idle_tick=0.1), 2))
    assert isinstance(got[0], np.ndarray)  # первой пришла фраза, а не None
    assert got[1] is None                  # тики пошли только после неё
