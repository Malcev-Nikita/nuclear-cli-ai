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

# large-v3-turbo — лучший русский при ~1.5 ГБ VRAM; small путает сленг и имена
# артистов (проверено вживую). На слабой машине: WHISPER_MODEL=small.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")  # auto | cuda | cpu
WHISPER_BEAM = int(os.environ.get("WHISPER_BEAM", "5"))  # 1 = жадный (быстрее, но хуже имена)
# Имена, на которые откликается ассистент (через запятую, регистр не важен).
ASSISTANT_NAMES = [
    n.strip().lower().replace("ё", "е")
    for n in os.environ.get("ASSISTANT_NAMES", "мага").split(",")
    if n.strip()
]
MIC_DEVICE = os.environ.get("MIC_DEVICE")  # индекс или подстрока имени; пусто = дефолтный
# Голос piper для озвучки ответов; пустая строка = TTS выключен.
PIPER_VOICE = os.environ.get("PIPER_VOICE", "ru_RU-irina-medium")
VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

SAMPLE_RATE = 16000  # частота, которую ест whisper
BLOCK = 512  # 32 мс на блок
PRE_ROLL_SEC = 0.4  # хвост до срабатывания VAD, чтобы не резать первый слог
SILENCE_END_SEC = 1.1  # столько тишины закрывает фразу (меньше — режет команды на полуслове)
MAX_UTTER_SEC = 12.0
MIN_UTTER_SEC = 0.4
VAD_GAIN = 3.0  # речь = громче адаптивного шумового пола во столько раз
VAD_ABS_MIN = 0.006  # но не тише этого RMS (защита от «речи» в полной тишине)
FOLLOWUP_SEC = 4.0  # после «Мага» без команды столько секунд ждём команду без имени

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
    """Ищет имя ассистента в любом месте фразы (VAD может склеить несколько
    предложений — «Играй. Он делает. Мага, стоп» должно сработать).

    Возвращает: команду после имени (последнее вхождение с продолжением);
    "" если после имени слов нет («Мага!»); None, если имени нет вовсе.
    """
    words = _normalize_words(text)
    hits = [i for i, word in enumerate(words) if _is_name(word, names)]
    if not hits:
        return None
    for i in reversed(hits):
        tail = words[i + 1:]
        if tail:
            return " ".join(tail)
    return ""


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

    def flush(self) -> None:
        """Выбросить накопленное аудио (например, собственную озвучку из колонок)."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

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

def _add_cuda_dll_dirs() -> None:
    """ctranslate2 на Windows не находит cublas/cudnn сам.

    Если стоят pip-пакеты nvidia-cublas-cu12 / nvidia-cudnn-cu12, их bin-папки
    (site-packages/nvidia/*/bin) надо добавить в поиск DLL руками.
    """
    if sys.platform != "win32":
        return
    import site
    from pathlib import Path

    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        for bin_dir in Path(root).glob("nvidia/*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        if WHISPER_DEVICE in ("auto", "cuda"):
            _add_cuda_dll_dirs()
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
                self._warmup(model)  # CUDA-DLL могут отвалиться только на первом encode
                self.model, self.device = model, "cuda"
                return
            except Exception as error:
                if WHISPER_DEVICE == "cuda":
                    raise
                reason = str(error).splitlines()[0]
                print(f"   (cuda не завёлся: {reason} — работаю на cpu; для gpu:")
                print("    python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12)")
        self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        self._warmup(self.model)
        self.device = "cpu"

    @staticmethod
    def _warmup(model) -> None:
        import numpy as np

        segments, _ = model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype="float32"),
                                       language="ru", beam_size=1)
        list(segments)

    def transcribe(self, audio) -> str:
        # Без initial_prompt: whisper на шуме/музыке «эхом» дописывал текст
        # подсказки, и ассистент сам себе командовал «Мага, включи музыку»
        # (подтверждено вживую 2026-08-01). Вместо подсказки — фильтр по
        # уверенности: галлюцинации приходят с низким avg_logprob.
        segments, _ = self.model.transcribe(
            audio,
            language="ru",
            beam_size=WHISPER_BEAM,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(
            s.text.strip() for s in segments
            if s.no_speech_prob < 0.7 and s.avg_logprob > -1.2
        ).strip()


# --- TTS: озвучка ответов через piper ---------------------------------------

class Speaker:
    """piper (piper-tts / piper1-gpl) с голосом PIPER_VOICE.

    Модель голоса (~60 МБ) скачивается в voices/ при первом запуске.
    """

    def __init__(self):
        import subprocess

        from piper import PiperVoice

        model_path = os.path.join(VOICES_DIR, f"{PIPER_VOICE}.onnx")
        if not os.path.exists(model_path):
            os.makedirs(VOICES_DIR, exist_ok=True)
            print(f"… скачиваю голос {PIPER_VOICE}", flush=True)
            subprocess.run(
                [sys.executable, "-m", "piper.download_voices", PIPER_VOICE],
                cwd=VOICES_DIR, check=True,
            )
        self.voice = PiperVoice.load(model_path)

    def say(self, text: str) -> None:
        import numpy as np
        import sounddevice as sd

        text = _sanitize_for_tts(text)
        if not text:
            return
        chunks = list(self.voice.synthesize(text))
        if not chunks:
            return
        audio = np.frombuffer(
            b"".join(c.audio_int16_bytes for c in chunks), dtype=np.int16,
        )
        sd.play(audio, samplerate=chunks[0].sample_rate, blocking=True)


def _sanitize_for_tts(text: str) -> str:
    """Убираем символы, которые piper прочитает вслух как мусор (▶, кавычки-ёлочки ок)."""
    text = re.sub(r"[▶⏸⏹🎤·]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# --- главный цикл -----------------------------------------------------------

def _beep():
    try:
        import winsound

        winsound.Beep(880, 120)
    except Exception:
        print("\a", end="", flush=True)


def _resolve_mic():
    if not MIC_DEVICE:
        return None  # None = системный микрофон по умолчанию (Windows/Linux)
    if MIC_DEVICE.isdigit():
        return int(MIC_DEVICE)
    return MIC_DEVICE  # sounddevice сам матчит подстроку имени


def _mic_name(device) -> str:
    import sounddevice as sd

    if device is None:
        return f"{sd.query_devices(kind='input')['name']} (системный по умолчанию)"
    return sd.query_devices(device, kind="input")["name"]


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
    agent.warmup_async()  # Ollama грузит модель параллельно с загрузкой whisper

    print(f"… загружаю whisper «{WHISPER_MODEL}»", flush=True)
    started = time.monotonic()
    stt = Transcriber()
    print(f"✔ Whisper {WHISPER_MODEL} на {stt.device} за {time.monotonic() - started:.1f}s")

    speaker = None
    if PIPER_VOICE:
        try:
            speaker = Speaker()
            print(f"✔ Голос: {PIPER_VOICE}")
        except Exception as error:
            print(f"   (TTS выключен: {str(error).splitlines()[0]}")
            print("    для озвучки: python -m pip install piper-tts)")

    names = ", ".join(n.capitalize() for n in ASSISTANT_NAMES)
    mic_device = _resolve_mic()
    print(f"🎙 Микрофон: {_mic_name(mic_device)}")
    print(f"Имя: {names}. Скажи «{ASSISTANT_NAMES[0].capitalize()}, включи …». Ctrl+C — выход.\n")

    follow_until = 0.0
    with MicSegmenter(mic_device) as mic:
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
                print(f"🎤 {text} — слушаю…")
                if speaker:
                    speaker.say("Слушаю")
                    mic.flush()  # не слушать собственный голос из колонок
                else:
                    _beep()
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
                if speaker:
                    speaker.say(answer)
                    mic.flush()  # не слушать собственный голос из колонок


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
