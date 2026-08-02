"""Управление воспроизведением: пауза/стоп/дальше/громкость/перемотка/что играет."""

from __future__ import annotations

import re

from src.core.texts import fmt_time, fmt_track, spoken_duration
from src.services.nuclear import NuclearPlayer
from src.skills.base import Rule, Skill, Tool


class PlaybackSkill(Skill):
    def __init__(self, player: NuclearPlayer):
        self.player = player

    # -- действия (фразы «под озвучку»)

    def pause(self) -> str:
        self.player.pause()
        return "Пауза"

    def resume(self) -> str:
        self.player.resume()
        return "Продолжаю"

    def stop(self) -> str:
        self.player.stop()
        return "Остановил"

    def next_track(self) -> str:
        self.player.next_track()
        return "Следующий трек"

    def previous_track(self) -> str:
        self.player.previous_track()
        return "Предыдущий трек"

    def now_playing(self) -> str:
        track = self.player.current_track()
        if not track:
            return "Сейчас ничего не играет"
        state = self.player.state()
        status = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(state.get("status"), "")
        position = ""
        if state.get("duration"):
            position = f" ({fmt_time(state.get('seek', 0))} / {fmt_time(state['duration'])})"
        return f"{status} {fmt_track(track)}{position}".strip()

    def set_volume(self, level: int) -> str:
        level = max(0, min(100, int(level)))
        self.player.set_volume_pct(level)
        return f"Громкость {level}%"

    def change_volume(self, delta: int) -> str:
        return self.set_volume(self.player.volume_pct() + delta)

    def set_shuffle(self, enabled: bool) -> str:
        self.player.set_shuffle(enabled)
        return "Перемешиваю" if enabled else "Играю по порядку"

    def set_repeat(self, mode: str) -> str:
        self.player.set_repeat(mode)
        return {"one": "Повторяю трек", "all": "Повторяю очередь"}.get(mode, "Повтор выключен")

    def seek_by(self, delta_seconds: int) -> str:
        state = self.player.state()
        if not state.get("duration") and state.get("status") not in ("playing", "paused"):
            return "Сейчас ничего не играет"
        position = max(0.0, float(state.get("seek") or 0) + delta_seconds)
        duration = float(state.get("duration") or 0)
        if duration:
            position = min(position, max(0.0, duration - 1))
        self.player.seek_to(position)
        direction = "вперёд" if delta_seconds >= 0 else "назад"
        return f"Перемотал {direction} на {spoken_duration(abs(delta_seconds))}"

    def seek_to_start(self) -> str:
        self.player.seek_to(0)
        return "С начала"

    def _seek_command(self, verb: str, rest: str) -> str:
        """«перемотай на 30 секунд вперёд» / «отмотай минуту» / «в начало»."""
        if re.search(r"начал|сначала|заново", rest):
            return self.seek_to_start()
        sign = -1 if verb.startswith("отмот") or re.search(r"назад|обратно", rest) else 1
        match = re.search(r"(\d+)\s*(секунд\w*|минут\w*|час\w*)?", rest)
        if match:
            amount = int(match.group(1))
            unit = match.group(2) or "секунд"
            factor = 3600 if unit.startswith("час") else 60 if unit.startswith("минут") else 1
            return self.seek_by(sign * amount * factor)
        if "полминуты" in rest:
            return self.seek_by(sign * 30)
        if re.search(r"минут", rest):
            return self.seek_by(sign * 60)
        return self.seek_by(sign * 30)  # без числа — шаг 30 секунд

    # -- регистрация

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(пауза|стоп музыка|подожди|pause)$", lambda m: self.pause()),
            # «замолчи»/«заткнись» тут нет — они затыкают озвучку (audio/tts), не музыку
            Rule(r"^(стоп|стой|остановись|хватит|выключи|stop)$", lambda m: self.stop()),
            Rule(r"^(играй|продолжи|продолжай|воспроизведи|play|плей)$", lambda m: self.resume()),
            # «включи музыку» без уточнений = продолжить очередь
            Rule(r"^(?:включ\w+|поставь|играй|запусти)\s+музыку$", lambda m: self.resume()),
            Rule(r"^(дальше|следующ\w*|скип|пропусти|next|skip)$", lambda m: self.next_track()),
            Rule(r"^(назад|предыдущ\w*|prev|back)$", lambda m: self.previous_track()),
            Rule(r"^(перемотай|промотай|отмотай)\s*(.*)$",
                 lambda m: self._seek_command(m.group(1), m.group(2))),
            Rule(r"^(?:с начала|сначала|заново)$", lambda m: self.seek_to_start()),
            Rule(r"^(громче|погромче)$", lambda m: self.change_volume(+10)),
            Rule(r"^(тише|потише)$", lambda m: self.change_volume(-10)),
            Rule(r"^(?:громкость|звук)\s+(\d{1,3})", lambda m: self.set_volume(int(m.group(1)))),
            Rule(r"^(что играет|что сейчас играет|now playing)\??$", lambda m: self.now_playing()),
            Rule(r"^(?:включи\s+)?(перемешай|перемешивание|вперемешку|шафл|shuffle)$",
                 lambda m: self.set_shuffle(True)),
            Rule(r"^(?:играй\s+)?(по порядку|без шафла|выключи перемешивание)$",
                 lambda m: self.set_shuffle(False)),
            # повтор: «этот трек» -> one, иначе вся очередь
            Rule(r"^(?:включи\s+|поставь\s+(?:на\s+)?)?повтор(?:\s+(трека|очереди))?$",
                 lambda m: self.set_repeat("one" if m.group(1) == "трека" else "all")),
            Rule(r"^(?:повтори|зацикли|поставь на повтор)\s+(?:этот\s+)?(?:трек|песню)$",
                 lambda m: self.set_repeat("one")),
            Rule(r"^(?:выключи|отключи)\s+повтор$", lambda m: self.set_repeat("off")),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("pause", "Поставить на паузу", lambda a: self.pause()),
            Tool("resume", "Продолжить воспроизведение", lambda a: self.resume()),
            Tool("next_track", "Переключить на следующий трек", lambda a: self.next_track()),
            Tool("previous_track", "Вернуться к предыдущему треку", lambda a: self.previous_track()),
            Tool("now_playing", "Сказать, что сейчас играет", lambda a: self.now_playing()),
            Tool("set_volume", "Установить громкость в процентах",
                 lambda a: self.set_volume(int(a["level"])),
                 params={"level": {"type": "integer", "description": "0-100"}},
                 required=["level"]),
            Tool("set_repeat", "Включить или выключить повтор",
                 lambda a: self.set_repeat(str(a.get("mode", "all"))),
                 params={"mode": {"type": "string",
                                  "description": "one — повтор трека, all — очереди, off — выключить"}},
                 required=["mode"]),
            Tool("set_shuffle", "Включить или выключить перемешивание очереди",
                 lambda a: self.set_shuffle(bool(a.get("enabled", True))),
                 params={"enabled": {"type": "boolean", "description": "true — перемешивать"}},
                 required=["enabled"]),
            Tool("seek_by", "Перемотать текущий трек на N секунд (отрицательное N — назад)",
                 lambda a: self.seek_by(int(a["seconds"])),
                 params={"seconds": {"type": "integer", "description": "Например 30 или -30"}},
                 required=["seconds"]),
        ]
