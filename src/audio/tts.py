"""Озвучка ответов: piper + умное перебивание (barge-in)."""

from __future__ import annotations

import os
import re
import sys

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


def _sanitize_for_tts(text: str) -> str:
    text = re.sub(r"[▶⏸⏹🎤·]", "", text)
    return re.sub(r"\s+", " ", text).strip()


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
        chunks = list(self.voice.synthesize(text, syn_config=self._config))
        if not chunks:
            return None
        audio = np.frombuffer(
            b"".join(c.audio_int16_bytes for c in chunks), dtype=np.int16,
        )
        sd.play(audio, samplerate=chunks[0].sample_rate)
        if mic is None or not BARGE_GAIN:
            sd.wait()
            return None

        # Сброс звука, записанного ДО начала речи: иначе эталон эха меряется
        # по тишине и ассистент перебивает сам себя на длинных ответах.
        mic.flush()
        skip_blocks = int(0.2 * SAMPLE_RATE / BLOCK)  # задержка колонки->микрофон
        baseline_blocks = int(0.4 * SAMPLE_RATE / BLOCK)  # замер своего эха
        need_loud = max(1, int(0.25 * SAMPLE_RATE / BLOCK))
        recent_cap = int(1.5 * SAMPLE_RATE / BLOCK)
        echo_levels: list[float] = []
        recent: list = []
        seen = 0
        loud_streak = 0
        ignore_until = 0
        stream = sd.get_stream()
        try:
            while stream.active:
                block = mic.get_block(timeout=0.1)
                if block is None:
                    continue
                seen += 1
                if seen <= skip_blocks:
                    continue
                rms = float(np.sqrt(np.mean(block**2)))
                if len(echo_levels) < baseline_blocks:
                    echo_levels.append(rms)
                    continue
                recent.append(block)
                if len(recent) > recent_cap:
                    recent.pop(0)
                if seen < ignore_until:
                    continue
                baseline = max(sum(echo_levels) / len(echo_levels), VAD_ABS_MIN)
                loud_streak = loud_streak + 1 if rms > baseline * BARGE_GAIN else 0
                if loud_streak < need_loud:
                    continue

                # Громкая речь поверх — дослушиваем ~1 с и проверяем, нам ли.
                for _ in range(int(1.0 * SAMPLE_RATE / BLOCK)):
                    if not stream.active:
                        break
                    extra = mic.get_block(timeout=0.1)
                    if extra is not None:
                        recent.append(extra)
                if stt is None:
                    sd.stop()
                    return None
                heard = stt.transcribe(np.concatenate(recent))
                words = normalize_words(heard)
                if self.wake.has_shutup_word(words):
                    sd.stop()
                    print("   🤫")
                    return None
                if self.wake.has_name(words):
                    sd.stop()
                    return heard
                loud_streak = 0  # чужой разговор — говорим дальше
                recent = []
                ignore_until = seen + int(1.0 * SAMPLE_RATE / BLOCK)
        finally:
            mic.flush()
        return None


def beep() -> None:
    try:
        import winsound

        winsound.Beep(880, 120)
    except Exception:
        print("\a", end="", flush=True)
