"""Загрузчик настроек: config.toml (дефолты) <- config.local.toml (личные)
<- переменные окружения (сильнее всех).

Сами настройки — в config.toml, тут только код загрузки. Имена констант
сохранены прежними — остальной код ничего не заметил.
"""

from __future__ import annotations

import os
import re
import tomllib

_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_toml(name: str) -> dict:
    try:
        with open(os.path.join(_DIR, name), "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as error:
        raise SystemExit(f"Ошибка в {name}: {error}")


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


_cfg = _merge(_load_toml("config.toml"), _load_toml("config.local.toml"))


def _get(section: str, key: str, env: str | None = None, default=None):
    if env and os.environ.get(env) is not None:
        return os.environ[env]
    return _cfg.get(section, {}).get(key, default)


def _words_regex(words: list[str]) -> re.Pattern:
    """Список слов -> регулярка полного совпадения. «следующ*» = любое окончание."""
    parts = [
        re.escape(w.strip().lower()[:-1]) + r"\w+" if w.endswith("*")
        else re.escape(w.strip().lower())
        for w in words if w.strip()
    ]
    return re.compile(r"^(" + "|".join(parts) + r")$")


# --- подключения ------------------------------------------------------------

NUCLEAR_MCP_URL = _get("connections", "nuclear_mcp_url", "NUCLEAR_MCP_URL")
OLLAMA_URL = _get("connections", "ollama_url", "OLLAMA_URL")
OLLAMA_MODEL = _get("connections", "ollama_model", "OLLAMA_MODEL")
OLLAMA_KEEP_ALIVE = _get("connections", "ollama_keep_alive", "OLLAMA_KEEP_ALIVE")
HTTP_TIMEOUT = int(_get("connections", "http_timeout", default=90))

# --- имя и поведение ассистента ---------------------------------------------

_names_raw = _get("assistant", "names", default=["мага"])
if isinstance(_names_raw, str):  # из env приходит строкой через запятую
    _names_raw = _names_raw.split(",")
if os.environ.get("ASSISTANT_NAMES"):
    _names_raw = os.environ["ASSISTANT_NAMES"].split(",")
ASSISTANT_NAMES = [
    n.strip().lower().replace("ё", "е") for n in _names_raw if n.strip()
]

FOLLOWUP_SEC = float(_get("assistant", "followup_sec", default=8.0))
BARE_COMMANDS = _words_regex(_get("assistant", "bare_commands", default=[]))
SHUTUP_COMMANDS = _words_regex(_get("assistant", "shutup_commands", default=[]))
CONTEXT_COMMAND_MAX_WORDS = int(_get("assistant", "context_command_max_words", default=8))
BARGE_GAIN = float(_get("assistant", "barge_gain", default=2.5))

# --- распознавание речи (faster-whisper) ------------------------------------

WHISPER_MODEL = _get("whisper", "model", "WHISPER_MODEL")
WHISPER_DEVICE = _get("whisper", "device", "WHISPER_DEVICE")
WHISPER_BEAM = int(_get("whisper", "beam", "WHISPER_BEAM", default=5))

# --- микрофон и нарезка речи (VAD) ------------------------------------------

MIC_DEVICE = _get("microphone", "device", "MIC_DEVICE") or None
SAMPLE_RATE = int(_get("microphone", "sample_rate", default=16000))
BLOCK = int(_get("microphone", "block", default=512))
PRE_ROLL_SEC = float(_get("microphone", "pre_roll_sec", default=0.4))
SILENCE_END_SEC = float(_get("microphone", "silence_end_sec", default=0.9))
SILENCE_END_LONG_SEC = float(_get("microphone", "silence_end_long_sec", default=0.9))
LONG_PHRASE_SEC = float(_get("microphone", "long_phrase_sec", default=1.5))
MAX_UTTER_SEC = float(_get("microphone", "max_utter_sec", default=12.0))
MIN_UTTER_SEC = float(_get("microphone", "min_utter_sec", default=0.4))
VAD_GAIN = float(_get("microphone", "vad_gain", default=2.5))
VAD_ABS_MIN = float(_get("microphone", "vad_abs_min", default=0.004))
MAX_LAG_SEC = float(_get("microphone", "max_lag_sec", default=3.0))

# --- озвучка ответов (piper) ------------------------------------------------

PIPER_VOICE = _get("tts", "voice", "PIPER_VOICE")
TTS_SPEED = float(_get("tts", "speed", "TTS_SPEED", default=1.25))
VOICES_DIR = os.path.join(_DIR, "voices")

# --- интернет-инструменты ---------------------------------------------------

WEATHER_CITY = _get("internet", "weather_city", "WEATHER_CITY", default="")
