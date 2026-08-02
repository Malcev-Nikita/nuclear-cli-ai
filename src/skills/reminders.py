"""Таймеры, будильники, напоминания: «поставь таймер на 10 минут»,
«напомни через час выключить духовку», «разбуди в 7 утра»."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from src.core.texts import plural, spoken_clock, spoken_duration
from src.services.reminders import Reminder, Reminders
from src.skills.base import Rule, Skill, Tool

_WORD_NUM = {
    "одну": 1, "один": 1, "одна": 1, "пару": 2, "две": 2, "два": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
}
_UNIT_SECONDS = [("сек", 1), ("мин", 60), ("час", 3600)]
_UNITS = r"(?:секунд\w*|сек|минут\w*|мин|час\w*|полчаса|полминуты)"
# «через 10 минут …» / «на полтора часа …» — хвост после срока = текст напоминания
_DELAY_RE = re.compile(rf"(?:через|спустя)\s+(.*?\b{_UNITS})\b", re.IGNORECASE)
_FOR_RE = re.compile(rf"^(?:на\s+)?(.*?\b{_UNITS})\b", re.IGNORECASE)
# «в|на» необязательны: правило роутера их уже съело («будильник НА 6:30» -> «6:30»)
_CLOCK_RE = re.compile(
    r"(?:(?:в|к|на)\s+)?(\d{1,2})(?:[:.\s](\d{2}))?\s*(утра|вечера|дня|ночи)?", re.IGNORECASE)


def _digits(text: str) -> str:
    """«полтора часа» -> «90 минут», «пять минут» -> «5 минут»."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"полтора\s+часа", "90 минут", text)
    text = text.replace("полчаса", "30 минут").replace("полминуты", "30 секунд")
    for word, number in _WORD_NUM.items():
        text = re.sub(rf"\b{word}\b", str(number), text)
    # единица без числа («час 30 минут», «через минуту») = одна штука
    return re.sub(r"(?<!\d\s)\b(час|минуту|минута|секунду)\b", r"1 \1", text)


def parse_delay(text: str) -> int | None:
    """«10 минут», «час 30 минут», «полтора часа» -> секунды."""
    text = _digits(text)
    total = 0
    for amount, unit in re.findall(rf"(\d+)\s*({_UNITS})", text):
        factor = next((f for prefix, f in _UNIT_SECONDS if unit.startswith(prefix)), 1)
        total += int(amount) * factor
    if not total:  # «через час», «через минуту» — без числа
        if re.search(r"\bчас\w*", text):
            total = 3600
        elif re.search(r"минут\w*", text):
            total = 60
    return total or None


def split_delay(text: str) -> tuple[int, str] | None:
    """«через час выключить духовку» -> (3600, «выключить духовку»)."""
    match = _DELAY_RE.search(text)
    if not match:
        return None
    seconds = parse_delay(match.group(1))
    if not seconds:
        return None
    rest = (text[:match.start()] + " " + text[match.end():]).strip(" ,.")
    return seconds, re.sub(r"\s+", " ", rest)


def split_clock(text: str, now: datetime | None = None) -> tuple[datetime, str] | None:
    """«в 7 утра подъём» -> (ближайшие 7:00, «подъём»). Прошедшее время = завтра."""
    now = now or datetime.now()
    match = _CLOCK_RE.search(_digits(text))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    part = (match.group(3) or "").lower()
    if part in ("вечера", "дня") and hour < 12:
        hour += 12
    elif part == "ночи" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    rest = (text[:match.start()] + " " + text[match.end():])
    if re.search(r"завтра", rest, re.IGNORECASE):
        target += timedelta(days=1)
    elif target <= now:
        target += timedelta(days=1)  # «в 7», когда уже 9 — значит, завтра в 7
    rest = re.sub(r"\b(завтра|сегодня|утра|вечера|дня|ночи)\b", "", rest, flags=re.IGNORECASE)
    return target, re.sub(r"\s+", " ", rest).strip(" ,.")


def announce(item: Reminder) -> str:
    """Фраза в момент срабатывания."""
    if item.kind == "таймер":
        return f"Таймер, {item.text}" if item.text else "Таймер! Время вышло"
    if item.kind == "будильник":
        return f"Подъём! {spoken_clock(item.when().hour, item.when().minute)}"
    return f"Напоминаю: {item.text}" if item.text else "Напоминаю"


class RemindersSkill(Skill):
    def __init__(self, store: Reminders):
        self.store = store

    # -- постановка

    def set_timer(self, spec: str) -> str:
        parsed = split_delay(spec) or _split_bare(spec)
        if not parsed:
            return "Не понял, на сколько ставить таймер"
        seconds, text = parsed
        self.store.add(datetime.now() + timedelta(seconds=seconds), text, "таймер")
        tail = f" — {text}" if text else ""
        return f"Поставил таймер на {spoken_duration(seconds)}{tail}"

    def remind(self, spec: str) -> str:
        by_clock = split_clock(spec)
        by_delay = split_delay(spec)
        if by_delay:
            seconds, text = by_delay
            at = datetime.now() + timedelta(seconds=seconds)
            when = f"через {spoken_duration(seconds)}"
        elif by_clock:
            at, text = by_clock
            when = f"в {spoken_clock(at.hour, at.minute)}"
            if at.date() != datetime.now().date():
                when += " завтра"
        else:
            return "Не понял, когда напомнить"
        text = re.sub(r"^(?:мне\s+)?(?:что\s+)?", "", text).strip(" ,.")
        if not text:
            return "А о чём напомнить?"
        self.store.add(at, text, "напоминание")
        return f"Напомню {when}: {text}"

    def set_alarm(self, spec: str) -> str:
        parsed = split_clock(spec)
        if not parsed:
            return "Не понял, на какое время будильник"
        at, text = parsed
        self.store.add(at, text, "будильник")
        return f"Разбужу в {spoken_clock(at.hour, at.minute)}"

    # -- просмотр и отмена

    def list_pending(self) -> str:
        items = self.store.pending()
        if not items:
            return "Ничего не поставлено"
        parts = []
        for item in items:
            left = item.at - datetime.now().timestamp()
            if item.kind == "будильник":
                parts.append(f"будильник на {spoken_clock(item.when().hour, item.when().minute)}")
            else:
                tail = f" ({item.text})" if item.text else ""
                # округляем: иначе «10 минут» превращается в «9 минут 59 секунд»
                parts.append(f"{item.kind} через {spoken_duration(max(1, round(left)))}{tail}")
        return f"{len(items)} {plural(len(items), 'штука', 'штуки', 'штук')}: " + ", ".join(parts)

    def cancel(self, what: str = "") -> str:
        kind = ""
        if re.search(r"таймер", what, re.IGNORECASE):
            kind = "таймер"
        elif re.search(r"будильник", what, re.IGNORECASE):
            kind = "будильник"
        elif re.search(r"напоминан", what, re.IGNORECASE):
            kind = "напоминание"
        removed = self.store.cancel(kind)
        if not removed:
            return "Нечего отменять"
        if len(removed) == 1:
            return f"Отменил {removed[0].kind}"
        return f"Отменил {len(removed)} {plural(len(removed), 'штуку', 'штуки', 'штук')}"

    # -- регистрация

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:поставь|заведи|включи|запусти)?\s*таймер\s*(?:на\s+)?(.*)$",
                 lambda m: self.set_timer(m.group(1))),
            Rule(r"^напомни(?:\s+мне)?\s+(.+)$", lambda m: self.remind(m.group(1))),
            Rule(r"^(?:разбуди(?:\s+меня)?|подними)\s+(.+)$", lambda m: self.set_alarm(m.group(1))),
            Rule(r"^(?:поставь|заведи|включи)\s+будильник\s+(?:на\s+)?(.+)$",
                 lambda m: self.set_alarm(m.group(1))),
            Rule(r"^(?:какие\s+|что\s+)?(?:у меня\s+)?"
                 r"(?:таймеры|будильники|напоминания|сколько осталось)\s*\??$",
                 lambda m: self.list_pending()),
            Rule(r"^(?:отмени|убери|выключи|сбрось|удали)\s+"
                 r"(таймер\w*|будильник\w*|напоминани\w*|все|всё)\s*$",
                 lambda m: self.cancel(m.group(1))),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("set_timer", "Поставить таймер на заданное время («на 10 минут»)",
                 lambda a: self.set_timer(str(a.get("duration", ""))),
                 params={"duration": {"type": "string", "description": "«10 минут», «полтора часа»"}},
                 required=["duration"], query_arg="duration"),
            Tool("set_reminder", "Напомнить о деле через время или в час дня",
                 lambda a: self.remind(f"{a.get('when', '')} {a.get('text', '')}".strip()),
                 params={"when": {"type": "string", "description": "«через час», «в 9 утра»"},
                         "text": {"type": "string", "description": "О чём напомнить"}},
                 required=["when", "text"]),
            Tool("set_alarm", "Поставить будильник на время суток",
                 lambda a: self.set_alarm(str(a.get("time", ""))),
                 params={"time": {"type": "string", "description": "«в 7 утра», «в 6:30»"}},
                 required=["time"], query_arg="time"),
            Tool("list_reminders", "Перечислить активные таймеры и будильники",
                 lambda a: self.list_pending()),
        ]

    def follow_up(self, text: str) -> str | None:
        """«а ещё на 5 минут» сразу после таймера."""
        if split_delay(text) or _split_bare(text):
            return self.set_timer(text)
        return None


def _split_bare(spec: str) -> tuple[int, str] | None:
    """«10 минут чайник» (без «через»/«на») -> (600, «чайник»)."""
    match = _FOR_RE.match(_digits(spec).strip())
    if not match:
        return None
    seconds = parse_delay(match.group(1))
    if not seconds:
        return None
    return seconds, re.sub(r"\s+", " ", _digits(spec).strip()[match.end():]).strip(" ,.")
