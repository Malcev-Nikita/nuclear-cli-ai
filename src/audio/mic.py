"""Микрофон + энергетический VAD: нарезка живой речи на фразы."""

from __future__ import annotations

import queue

from config import (
    BLOCK,
    LONG_PHRASE_SEC,
    MAX_LAG_SEC,
    MAX_UTTER_SEC,
    MIN_UTTER_SEC,
    PRE_ROLL_SEC,
    SAMPLE_RATE,
    SILENCE_END_LONG_SEC,
    SILENCE_END_SEC,
    VAD_ABS_MIN,
    VAD_GAIN,
)


class MicSegmenter:
    """Отдаёт законченные фразы как float32-массивы 16 кГц.

    Шумовой пол — экспоненциальное среднее RMS; речь = RMS выше пола в VAD_GAIN раз.
    """

    def __init__(self, device):
        import numpy as np
        import sounddevice as sd

        self._np = np
        self._queue: queue.Queue = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK,
            channels=1,
            dtype="float32",
            device=device,
            callback=lambda indata, *_: self._queue.put(indata.copy()),
        )
        self._noise = VAD_ABS_MIN

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *_):
        self._stream.stop()
        self._stream.close()

    def flush(self) -> None:
        """Выбросить накопленное аудио (например, собственную озвучку из колонок)."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def get_block(self, timeout: float):
        """Один блок аудио (для barge-in во время озвучки); None по таймауту."""
        try:
            return self._queue.get(timeout=timeout)[:, 0]
        except queue.Empty:
            return None

    def utterances(self):
        np = self._np
        pre_roll: list = []
        pre_roll_blocks = int(PRE_ROLL_SEC * SAMPLE_RATE / BLOCK)
        capture: list | None = None
        silent_blocks = 0
        speech_blocks = 0
        end_blocks_short = int(SILENCE_END_SEC * SAMPLE_RATE / BLOCK)
        end_blocks_long = int(SILENCE_END_LONG_SEC * SAMPLE_RATE / BLOCK)
        long_phrase_blocks = int(LONG_PHRASE_SEC * SAMPLE_RATE / BLOCK)
        max_blocks = int(MAX_UTTER_SEC * SAMPLE_RATE / BLOCK)
        max_lag_blocks = int(MAX_LAG_SEC * SAMPLE_RATE / BLOCK)

        while True:
            # Отстали от реального времени — выбрасываем старьё, слушаем «сейчас».
            if capture is None and self._queue.qsize() > max_lag_blocks:
                lag = self._queue.qsize() * BLOCK / SAMPLE_RATE
                self.flush()
                pre_roll = []
                print(f"   (отстал на {lag:.1f} с — пропускаю старый звук)")

            block = self._queue.get()[:, 0]
            rms = float(np.sqrt(np.mean(block**2)))
            threshold = max(VAD_ABS_MIN, self._noise * VAD_GAIN)
            is_speech = rms > threshold

            if capture is None:
                if is_speech:
                    capture = pre_roll + [block]
                    silent_blocks = 0
                    speech_blocks = 1
                else:
                    self._noise = 0.95 * self._noise + 0.05 * rms
                    pre_roll.append(block)
                    if len(pre_roll) > pre_roll_blocks:
                        pre_roll.pop(0)
                continue

            capture.append(block)
            # Медленная адаптация и во время записи: иначе непрерывная музыка
            # держит VAD «в речи» вечно — ассистент глохнет и копит отставание.
            self._noise = 0.998 * self._noise + 0.002 * rms
            if is_speech:
                silent_blocks = 0
                speech_blocks += 1
            else:
                silent_blocks += 1
            # Длинная фраза → терпим паузу подольше: «включи какой-нибудь…
            # ролик…» не режется на куски, а «дальше» закрывается быстро.
            end_blocks = end_blocks_long if speech_blocks >= long_phrase_blocks else end_blocks_short
            if silent_blocks >= end_blocks or len(capture) >= max_blocks:
                audio = np.concatenate(capture)
                capture = None
                pre_roll = []
                if len(audio) / SAMPLE_RATE >= MIN_UTTER_SEC + SILENCE_END_SEC:
                    yield audio


def list_devices() -> None:
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"  [{i}] {dev['name']}")


def resolve_mic(setting: str | None):
    if not setting:
        return None  # системный микрофон по умолчанию
    if setting.isdigit():
        return int(setting)
    return setting  # sounddevice сам матчит подстроку имени


def mic_name(device) -> str:
    import sounddevice as sd

    if device is None:
        return f"{sd.query_devices(kind='input')['name']} (системный по умолчанию)"
    return sd.query_devices(device, kind="input")["name"]
