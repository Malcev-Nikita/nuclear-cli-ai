#!/usr/bin/env python3
"""Nuclear CLI AI — этап 2: голосовой ввод.

Цепочка: микрофон → энергетический VAD (нарезка речи на фразы) → faster-whisper
(STT, русский) → фильтр «фраза начинается с имени ассистента» → agent.handle()
из assistant.py (роутер + LLM + Nuclear).

Wake word без отдельной модели: whisper и так распознаёт каждую фразу, а мы
реагируем только на те, что начинаются с имени. Имён может быть несколько
(ASSISTANT_NAMES, через запятую), сравнение нечёткое — «Егорь»/«Игор» тоже ловятся.

Запуск:  python voice.py            (--devices — список микрофонов)
Конфиг:  env WHISPER_MODEL, WHISPER_DEVICE, WHISPER_BEAM, ASSISTANT_NAMES,
         MIC_DEVICE + все переменные assistant.py.
"""

from __future__ import annotations

import os
import queue
import re
import sys
import time

import assistant

# --- конфиг -----------------------------------------------------------------

# small — разумный баланс для русского; на GPU можно medium или large-v3-turbo.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")  # auto | cuda | cpu
WHISPER_BEAM = int(os.environ.get("WHISPER_BEAM", "1"))  # 1 = жадный, минимум латентности
# Имена, на которые откликается ассистент (через запятую, регистр не важен).
ASSISTANT_NAMES = [
    n.strip().lower().replace("ё", "е")
    for n in os.environ.get("ASSISTANT_NAMES", "игорь").split(",")
    if n.strip()
]
MIC_DEVICE = os.environ.get("MIC_DEVICE")  # индекс или подстрока имени; пусто = дефолтный

SAMPLE_RATE = 16000  # частота, которую ест whisper
BLOCK = 512  # 32 мс на блок
PRE_ROLL_SEC = 0.4  # хвост до срабатывания VAD, чтобы не резать первый слог
SILENCE_END_SEC = 0.8  # столько тишины закрывает фразу
MAX_UTTER_SEC = 12.0
MIN_UTTER_SEC = 0.4
VAD_GAIN = 3.0  # речь = громче адаптивного шумового пола во столько раз
VAD_ABS_MIN = 0.006  # но не тише этого RMS (защита от «речи» в полной тишине)
FOLLOWUP_SEC = 8.0  # после «Игорь» без команды столько секунд ждём команду без имени

# Типичные галлюцинации whisper на шуме/музыке — не считаем их речью.
_JUNK = re.compile(
    r"субтитр|dimatorzok|редактор|продолжение следует|спасибо за просмотр",
    re.IGNORECASE,
)


# --- wake word: нечёткое совпадение имени в начале фразы ---------------------

def _normalize_words(text: str) -> list[str]:
    return re.sub(r"[^\wё]+", " ", text.lower().replace("ё", "е")).split()


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = row
    return prev[-1]


def _is_name(word: str, names: list[str] = ASSISTANT_NAMES) -> bool:
    for name in names:
        max_dist = 1 if len(name) >= 4 else 0
        if _levenshtein(word, name) <= max_dist:
            return True
    return False


def extract_command(text: str, names: list[str] = ASSISTANT_NAMES) -> str | None:
    """Ищет имя ассистента в первых словах фразы.

    Возвращает: команду после имени; "" если фраза — только имя («Игорь!»);
    None, если имени нет и фразу надо игнорировать.
    """
    words = _normalize_words(text)
    if not words:
        return None
    for i, word in enumerate(words[:3]):
        if _is_name(word, names):
            return " ".join(words[i + 1:])
    return None


def looks_like_junk(text: str) -> bool:
    return not re.search(r"[а-яa-z]", text.lower().replace("ё", "е")) or bool(_JUNK.search(text))


# --- микрофон + VAD ---------------------------------------------------------

class MicSegmenter:
    """Читает микрофон и отдаёт законченные фразы как float32-массивы 16 кГц.

    VAD энергетический: шумовой пол — экспоненциальное среднее RMS вне речи,
    речь = RMS больше пола в VAD_GAIN раз. Пока фраза пишется, пол не обновляется.
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

    def utterances(self):
        np = self._np
        pre_roll: list = []
        pre_roll_blocks = int(PRE_ROLL_SEC * SAMPLE_RATE / BLOCK)
        capture: list | None = None
        silent_blocks = 0
        end_blocks = int(SILENCE_END_SEC * SAMPLE_RATE / BLOCK)
        max_blocks = int(MAX_UTTER_SEC * SAMPLE_RATE / BLOCK)

        while True:
            block = self._queue.get()[:, 0]
            rms = float(np.sqrt(np.mean(block**2)))
            threshold = max(VAD_ABS_MIN, self._noise * VAD_GAIN)
            is_speech = rms > threshold

            if capture is None:
                if is_speech:
                    capture = pre_roll + [block]
                    silent_blocks = 0
                else:
                    self._noise = 0.95 * self._noise + 0.05 * rms
                    pre_roll.append(block)
                    if len(pre_roll) > pre_roll_blocks:
                        pre_roll.pop(0)
                continue

            capture.append(block)
            silent_blocks = 0 if is_speech else silent_blocks + 1
            if silent_blocks >= end_blocks or len(capture) >= max_blocks:
                audio = np.concatenate(capture)
                capture = None
                pre_roll = []
                if len(audio) / SAMPLE_RATE >= MIN_UTTER_SEC + SILENCE_END_SEC:
                    yield audio


# --- STT --------------------------------------------------------------------

class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        device = WHISPER_DEVICE
        compute = "float16" if device == "cuda" else "int8"
        if device == "auto":
            try:
                self.model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
                self.device = "cuda"
                return
            except Exception:
                device = "cpu"
                compute = "int8"
        self.model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute)
        self.device = device

    def transcribe(self, audio) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="ru",
            beam_size=WHISPER_BEAM,
            vad_filter=True,
            condition_on_previous_text=False,
            # Подсказка смещает распознавание к имени ассистента и командам.
            initial_prompt=f"{ASSISTANT_NAMES[0].capitalize()}, включи музыку. Пауза. Дальше.",
        )
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.7).strip()


# --- главный цикл -----------------------------------------------------------

def _beep():
    try:
        import winsound

        winsound.Beep(880, 120)
    except Exception:
        print("\a", end="", flush=True)


def _resolve_mic():
    if not MIC_DEVICE:
        return None
    if MIC_DEVICE.isdigit():
        return int(MIC_DEVICE)
    return MIC_DEVICE  # sounddevice сам матчит подстроку имени


def list_devices() -> None:
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"  [{i}] {dev['name']}")


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    if "--devices" in sys.argv:
        list_devices()
        return

    mcp = assistant.McpClient(assistant.NUCLEAR_MCP_URL)
    agent = assistant.Agent(assistant.Nuclear(mcp))

    print("Nuclear CLI AI — этап 2 (голос)")
    assistant.check_connections(mcp)

    print(f"… загружаю whisper «{WHISPER_MODEL}»", flush=True)
    started = time.monotonic()
    stt = Transcriber()
    print(f"✔ Whisper {WHISPER_MODEL} на {stt.device} за {time.monotonic() - started:.1f}s")
    names = ", ".join(n.capitalize() for n in ASSISTANT_NAMES)
    print(f"Слушаю микрофон. Имя: {names}. Скажи «{ASSISTANT_NAMES[0].capitalize()}, включи …». Ctrl+C — выход.\n")

    follow_until = 0.0
    with MicSegmenter(_resolve_mic()) as mic:
        for audio in mic.utterances():
            t0 = time.monotonic()
            text = stt.transcribe(audio)
            stt_ms = (time.monotonic() - t0) * 1000
            if not text or looks_like_junk(text):
                continue

            command = extract_command(text)
            if command is None and time.monotonic() < follow_until:
                command = " ".join(_normalize_words(text))
                follow_until = 0.0
            if command is None:
                print(f"   · мимо: «{text}»  [stt {stt_ms:.0f}ms]")
                continue
            if not command:
                _beep()
                print(f"🎤 {text} — слушаю…")
                follow_until = time.monotonic() + FOLLOWUP_SEC
                continue

            follow_until = 0.0
            print(f"🎤 «{text}»  [stt {stt_ms:.0f}ms]")
            t0 = time.monotonic()
            try:
                answer = agent.handle(command)
            except assistant.NuclearError as error:
                answer = f"Nuclear: {error}"
            except Exception as error:  # requests и прочее — не роняем цикл
                answer = f"Ошибка: {error}"
            if answer:
                print(f"   {answer}   [{time.monotonic() - t0:.1f}s]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
