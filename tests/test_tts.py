"""Потоковая озвучка: куски синтеза уходят в очередь по мере готовности."""

import queue
import threading

from src.audio.tts import _END, _sanitize_for_tts, _synthesize_into


class FakeChunk:
    def __init__(self, data: bytes):
        self.audio_int16_bytes = data
        self.sample_rate = 22050


class FakeVoice:
    """Отдаёт куски по одному, как piper по предложениям."""

    def __init__(self, pieces=(b"aa", b"bb", b"cc"), fail=False):
        self.pieces = pieces
        self.fail = fail
        self.started = threading.Event()

    def synthesize(self, text, syn_config=None):
        self.started.set()
        for piece in self.pieces:
            yield FakeChunk(piece)
        if self.fail:
            raise RuntimeError("голос сломался")


def drain(voice) -> list:
    out: queue.Queue = queue.Queue()
    _synthesize_into(voice, "текст", None, out)
    items = []
    while True:
        item = out.get_nowait()
        if item is _END:
            return items
        items.append(item)


def test_chunks_stream_in_order():
    assert drain(FakeVoice()) == [(b"aa", 22050), (b"bb", 22050), (b"cc", 22050)]


def test_end_marker_even_on_error():
    """Синтез упал — маркер конца всё равно приходит, иначе say() зависнет."""
    assert drain(FakeVoice(fail=True)) == [(b"aa", 22050), (b"bb", 22050), (b"cc", 22050)]


def test_first_chunk_available_before_synthesis_finishes():
    """Смысл потоковости: первый кусок можно играть, пока считаются остальные."""
    released = threading.Event()
    pieces: queue.Queue = queue.Queue()

    class SlowVoice(FakeVoice):
        def synthesize(self, text, syn_config=None):
            yield FakeChunk(b"first")
            released.wait(timeout=2)  # держим синтез второго куска
            yield FakeChunk(b"second")

    threading.Thread(target=_synthesize_into,
                     args=(SlowVoice(), "текст", None, pieces), daemon=True).start()
    assert pieces.get(timeout=2) == (b"first", 22050)  # не ждём конца синтеза
    released.set()
    assert pieces.get(timeout=2) == (b"second", 22050)


def test_sanitize_strips_icons():
    assert _sanitize_for_tts("▶ Band — Song (0:10 / 3:20)") == "Band — Song (0:10 / 3:20)"
    assert _sanitize_for_tts("  два   пробела ") == "два пробела"
