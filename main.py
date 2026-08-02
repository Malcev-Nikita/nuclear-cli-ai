#!/usr/bin/env python3
"""Nuclear CLI AI — голосовой ассистент для Nuclear («Яндекс станция» на ПК).

Запуск:  python main.py            голосовой режим
         python main.py --text     текстовый REPL (без микрофона и озвучки)
         python main.py --devices  список микрофонов
         python main.py --meter    уровень микрофона против порога VAD
"""

from __future__ import annotations

import sys
import time
from typing import NamedTuple

import requests

from src import config
from src.core.agent import Agent
from src.core.wake import WakeMatcher, looks_like_junk, normalize_words
from src.services.bitrix import Bitrix24
from src.services.nuclear import McpClient, NuclearError, NuclearPlayer
from src.services.ollama import OllamaBrain
from src.services.reminders import Reminders
from src.services.weather import OpenMeteoWeather
from src.services.websearch import DuckDuckGo
from src.services.youtube import YoutubeSearch
from src.skills.clock import ClockSkill
from src.skills.favorites import FavoritesSkill
from src.skills.music import MusicSkill
from src.skills.notes import NotesSkill
from src.skills.playback import PlaybackSkill
from src.skills.reminders import RemindersSkill, announce
from src.skills.search import SearchSkill
from src.skills.weather import WeatherSkill
from src.skills.worktime import WorktimeSkill
from src.skills.youtube import YoutubeSkill


REMINDER_TICK_SEC = 0.5  # как часто в тишине заглядывать в таймеры


class Parts(NamedTuple):
    """Собранный ассистент: агенту нужны все, циклу — плеер и напоминания."""
    mcp: McpClient
    player: NuclearPlayer
    brain: OllamaBrain
    agent: Agent
    reminders: Reminders


def build() -> Parts:
    """Сборка: сервисы -> навыки -> агент. Порядок навыков = приоритет роутера."""
    mcp = McpClient(config.NUCLEAR_MCP_URL)
    player = NuclearPlayer(mcp)
    brain = OllamaBrain()
    reminders = Reminders(config.REMINDERS_FILE)
    youtube = YoutubeSkill(player, YoutubeSearch())
    skills = [
        PlaybackSkill(player),
        FavoritesSkill(player),
        youtube,
        MusicSkill(player, youtube),
        WeatherSkill(OpenMeteoWeather(config.WEATHER_CITY)),
        ClockSkill(),
        RemindersSkill(reminders),
    ]
    if config.B24_WEBHOOK:
        skills.append(WorktimeSkill(Bitrix24()))
    skills += [NotesSkill(), SearchSkill(DuckDuckGo(), brain)]
    return Parts(mcp, player, brain, Agent(skills, brain), reminders)


def check_connections(mcp: McpClient, brain: OllamaBrain) -> None:
    try:
        mcp.handshake()
        state = mcp.call("Playback.getState")
        print(f"✔ Nuclear MCP: {config.NUCLEAR_MCP_URL} (playback: {state.get('status', '?')})")
    except Exception as error:
        print(f"✘ Nuclear MCP недоступен ({config.NUCLEAR_MCP_URL}): {error}")
        print("  Проверь: Nuclear запущен, Settings → Integrations → Enable MCP Server.")
        sys.exit(1)
    try:
        print(f"✔ Ollama {brain.version()}: модель {config.OLLAMA_MODEL}")
    except Exception as error:
        print(f"✘ Ollama недоступна ({config.OLLAMA_URL}): {error}")
        print("  Запусти Ollama и выполни: ollama pull " + config.OLLAMA_MODEL)
        sys.exit(1)


def _dispatch(agent: Agent, command: str) -> str:
    try:
        return agent.handle(command)
    except NuclearError as error:
        return f"Nuclear: {error}"
    except requests.RequestException as error:
        return f"Сеть: {error}"
    except Exception as error:  # не роняем цикл
        return f"Ошибка: {error}"


def run_text() -> None:
    parts = build()
    agent = parts.agent
    print("Nuclear CLI AI — текстовый режим")
    check_connections(parts.mcp, parts.brain)
    parts.brain.warmup_async()
    print("Примеры: «включи нирвану», «плейлист rock», «дальше», «какая погода»,")
    print("         «найди столицу австралии», «что играет». Выход: q\n")

    while True:
        # без микрофона тикать нечему — сработавшее показываем перед вводом
        for item in parts.reminders.due():
            print(f"⏰ {announce(item)}")
        try:
            text = input("🎤 > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.strip().lower() in ("q", "quit", "exit", "выход"):
            break
        started = time.monotonic()
        answer = _dispatch(agent, text)
        if answer:
            print(f"   {answer}   [{time.monotonic() - started:.1f}s]")


def run_voice() -> None:
    from src.audio.duck import Ducker
    from src.audio.mic import MicSegmenter, mic_name, resolve_mic
    from src.audio.stt import Transcriber
    from src.audio.tts import Speaker, beep

    parts = build()
    agent = parts.agent
    wake = WakeMatcher()
    ducker = Ducker(parts.player, config.DUCK_VOLUME_PCT)

    print("Nuclear CLI AI — голосовой режим")
    check_connections(parts.mcp, parts.brain)
    parts.brain.warmup_async()  # Ollama грузится параллельно с whisper

    print(f"… загружаю whisper «{config.WHISPER_MODEL}»", flush=True)
    started = time.monotonic()
    stt = Transcriber()
    print(f"✔ Whisper {config.WHISPER_MODEL} на {stt.device} за {time.monotonic() - started:.1f}s")

    speaker = None
    if config.PIPER_VOICE:
        try:
            speaker = Speaker(wake)
            print(f"✔ Голос: {config.PIPER_VOICE}")
        except Exception as error:
            print(f"   (TTS выключен: {str(error).splitlines()[0]}")
            print("    для озвучки: python -m pip install piper-tts)")

    mic_device = resolve_mic(config.MIC_DEVICE)
    names = ", ".join(n.capitalize() for n in wake.names)
    print(f"🎙 Микрофон: {mic_name(mic_device)}")
    print(f"Имя: {names}. Скажи «{wake.names[0].capitalize()}, включи …». Ctrl+C — выход.\n")

    follow = {"until": 0.0}
    with MicSegmenter(mic_device) as mic:

        def speak(phrase: str) -> None:
            with ducker.quiet():  # музыка тише, пока говорим
                heard = speaker.say(phrase, mic, stt)
            if heard:  # перебили с именем — это команда
                print(f"🎤 (перебил) «{heard}»")
                process(heard)

        def process(text: str, stt_ms: float | None = None) -> None:
            timing = f"  [stt {stt_ms:.0f}ms]" if stt_ms is not None else ""
            command = wake.extract_command(text)
            if command is None:
                words = normalize_words(text)
                normalized = " ".join(words)
                if time.monotonic() < follow["until"]:
                    command = normalized
                    follow["until"] = 0.0
                elif wake.bare.match(normalized) or wake.shutup.match(normalized):
                    command = normalized  # управляющая команда — можно без имени
                else:
                    command = wake.control_in_context(words)
            if command is None:
                print(f"   · мимо: «{text}»{timing}")
                return
            if wake.shutup.match(command):
                print("   🤫")  # оборвать речь и молчать; музыку не трогаем
                follow["until"] = 0.0
                return
            if not command:
                print(f"🎤 {text} — слушаю…")
                if speaker:
                    speak("Слушаю")
                else:
                    beep()
                follow["until"] = time.monotonic() + config.FOLLOWUP_SEC
                return

            follow["until"] = 0.0
            print(f"🎤 «{text}»{timing}")
            started = time.monotonic()
            answer = _dispatch(agent, command)
            if answer:
                print(f"   {answer}   [{time.monotonic() - started:.1f}s]")
                if speaker:
                    speak(answer)

        def ring() -> None:
            """Сработавшие таймеры/будильники — озвучиваем в главном потоке."""
            for item in parts.reminders.due():
                phrase = announce(item)
                print(f"⏰ {phrase}")
                beep()
                if speaker:
                    speak(phrase)

        # idle_tick: в тишине генератор отдаёт None — момент проверить таймеры
        for audio in mic.utterances(idle_tick=REMINDER_TICK_SEC):
            ring()
            if audio is None:
                continue
            started = time.monotonic()
            text = stt.transcribe(audio)
            stt_ms = (time.monotonic() - started) * 1000
            if not text or looks_like_junk(text):
                continue
            process(text, stt_ms)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    if "--devices" in argv:
        from src.audio.mic import list_devices
        list_devices()
        return
    if "--meter" in argv:
        from src.audio.mic import meter, mic_name, resolve_mic
        device = resolve_mic(config.MIC_DEVICE)
        print(f"🎙 Микрофон: {mic_name(device)}. Ctrl+C — выход.")
        try:
            meter(device)
        except KeyboardInterrupt:
            print()
        return
    try:
        if "--text" in argv:
            run_text()
        else:
            run_voice()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
