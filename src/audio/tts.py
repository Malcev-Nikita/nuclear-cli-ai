"""Озвучка ответов: piper + умное перебивание (barge-in).

Синтез потоковый: piper отдаёт куски (по предложениям), каждый играем сразу,
пока фоновый поток синтезирует следующий. Длинный ответ (пересказ поиска,
отчёт за месяц) начинает звучать почти мгновенно, а не после синтеза целиком.
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading

from src.config import (
    BARGE_GAIN,
    BLOCK,
    PIPER_VOICE,
    SAMPLE_RATE,
    TTS_SPEED,
    VAD_ABS_MIN,
    VOICES_DIR,
)
from src.core.wake import WakeMatcher, normalize_words
from src.audio.mic import MicSegmenter

_END = object()  # маркер «синтез закончен»


def _sanitize_for_tts(text: str) -> str:
    text = re.sub(r"[▶⏸⏹🎤·]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _synthesize_into(voice, text: str, config, out: queue.Queue) -> None:
    """Фоновый синтез: куски (байты, частота) складываются в очередь по мере готовности."""
    try:
        for chunk in voice.synthesize(text, syn_config=config):
            out.put((chunk.audio_int16_bytes, chunk.sample_rate))
    except Exception as error:  # не роняем голосовой цикл из-за озвучки
        print(f"   (озвучка сломалась: {error})")
    finally:
        out.put(_END)


class _BargeWatcher:
    """Слушает микрофон, пока играет озвучка.

    Состояние (эталон эха, буфер услышанного) живёт на всю реплику, а не на
    отдельный кусок синтеза — иначе эталон мерился бы заново на каждом
    предложении и ассистент перебивал бы сам себя.
    """

    def __init__(self, wake: WakeMatcher, mic: MicSegmenter, stt):
        self.wake, self.mic, self.stt = wake, mic, stt
        self.skip_blocks = int(0.2 * SAMPLE_RATE / BLOCK)      # задержка колонки->микрофон
        self.baseline_blocks = int(0.4 * SAMPLE_RATE / BLOCK)  # замер своего эха
        self.need_loud = max(1, int(0.25 * SAMPLE_RATE / BLOCK))
        self.recent_cap = int(1.5 * SAMPLE_RATE / BLOCK)
        self.listen_blocks = int(1.0 * SAMPLE_RATE / BLOCK)
        self.echo_levels: list[float] = []
        self.recent: list = []
        self.seen = 0
        self.loud_streak = 0
        self.ignore_until = 0
        self.flushed = False

    def watch(self) -> tuple[str, str | None]:
        """Крутится, пока играет текущий кусок.

        -> ("continue", None)  кусок доиграл, можно играть следующий
           ("shutup", None)    услышали «заткнись» — молчим
           ("name", текст)     услышали имя — это команда, её вернём наверх
        """
        import numpy as np
        import sounddevice as sd

        if not self.flushed:
            # Сброс звука, накопленного пока агент думал: иначе эталон эха
            # меряется по тишине и ассистент перебивает сам себя.
            self.mic.flush()
            self.flushed = True
        try:
            stream = sd.get_stream()
        except Exception:
            return "continue", None

        while stream.active:
            block = self.mic.get_block(timeout=0.1)
            if block is None:
                continue
            self.seen += 1
            if self.seen <= self.skip_blocks:
                continue
            rms = float(np.sqrt(np.mean(block**2)))
            if len(self.echo_levels) < self.baseline_blocks:
                self.echo_levels.append(rms)
                continue
            self.recent.append(block)
            if len(self.recent) > self.recent_cap:
                self.recent.pop(0)
            if self.seen < self.ignore_until:
                continue
            baseline = max(sum(self.echo_levels) / len(self.echo_levels), VAD_ABS_MIN)
            self.loud_streak = self.loud_streak + 1 if rms > baseline * BARGE_GAIN else 0
            if self.loud_streak < self.need_loud:
                continue

            # Громкая речь поверх — дослушиваем ~1 с и проверяем, нам ли.
            for _ in range(self.listen_blocks):
                if not stream.active:
                    break
                extra = self.mic.get_block(timeout=0.1)
                if extra is not None:
                    self.recent.append(extra)
            if self.stt is None:
                return "shutup", None
            heard = self.stt.transcribe(np.concatenate(self.recent))
            words = normalize_words(heard)
            if self.wake.has_shutup_word(words):
                return "shutup", None
            if self.wake.has_name(words):
                return "name", heard
            self.loud_streak = 0  # чужой разговор — говорим дальше
            self.recent = []
            self.ignore_until = self.seen + self.listen_blocks
        return "continue", None


class Speaker:
    """piper с голосом PIPER_VOICE (модель ~60 МБ качается при первом запуске)."""

    def __init__(self, wake: WakeMatcher):
        import subprocess

        from piper import PiperVoice, SynthesisConfig

        self.wake = wake
        model_path = os.path.join(VOICES_DIR, f"{PIPER_VOICE}.onnx")
        if not os.path.exists(model_path):
            os.makedirs(VOICES_DIR, exist_ok=True)
            print(f"… скачиваю голос {PIPER_VOICE}", flush=True)
            subprocess.run(
                [sys.executable, "-m", "piper.download_voices", PIPER_VOICE],
                cwd=VOICES_DIR, check=True,
            )
        self.voice = PiperVoice.load(model_path)
        self._config = SynthesisConfig(length_scale=1.0 / TTS_SPEED)  # 1/скорость

    def say(self, text: str, mic: MicSegmenter | None = None, stt=None) -> str | None:
        """Озвучить. С mic — умное перебивание: громкая речь поверх колонок
        дослушивается ~1 с и обрывает озвучку, только если там «заткнись» или
        имя ассистента (тогда услышанное возвращается — это команда). Чужой
        разговор рядом игнорируется."""
        import numpy as np
        import sounddevice as sd

        text = _sanitize_for_tts(text)
        if not text:
            return None
        pieces: queue.Queue = queue.Queue()
        threading.Thread(
            target=_synthesize_into, args=(self.voice, text, self._config, pieces),
            daemon=True,
        ).start()
        watcher = _BargeWatcher(self.wake, mic, stt) if mic is not None and BARGE_GAIN else None

        try:
            while True:
                piece = pieces.get()  # ждём кусок; первый — и есть задержка старта
                if piece is _END:
                    return None
                data, rate = piece
                sd.play(np.frombuffer(data, dtype=np.int16), samplerate=rate)
                if watcher is None:
                    sd.wait()
                    continue
                verdict, heard = watcher.watch()
                if verdict == "continue":
                    continue
                sd.stop()
                if verdict == "shutup":
                    print("   🤫")
                return heard
        finally:
            if mic is not None:
                mic.flush()


def beep() -> None:
    try:
        import winsound

        winsound.Beep(880, 120)
    except Exception:
        print("\a", end="", flush=True)
