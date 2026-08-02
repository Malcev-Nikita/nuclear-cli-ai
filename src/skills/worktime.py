"""Рабочее время из Битрикс24: «сколько я сегодня проработал», за период/день."""

from __future__ import annotations

import re
from datetime import date, timedelta

from src.core.texts import plural
from src.services.bitrix import Bitrix24, BitrixError
from src.skills.base import Rule, Skill, Tool

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_DATE_RE = re.compile(
    r"(\d{1,2})(?:-?го)?\s+"
    r"(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r"(?:\s+(\d{4})(?:\s*год\w*)?)?"
)
# месяц по имени без числа: «за июль», «в мае», «за июнь 2025»
_MONTH_RE = re.compile(
    r"(?<![а-яё])(январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r"(?:\s+(\d{4})(?:\s*год\w*)?)?"
)
_MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь",
                "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def _month_num(word: str) -> int:
    return next(n for prefix, n in _MONTHS.items() if word.startswith(prefix))


def _month_range(year: int, month: int) -> tuple[date, date]:
    frm = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return frm, nxt - timedelta(days=1)


def spoken_work(seconds: int) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    parts = []
    if hours:
        parts.append(f"{hours} {plural(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append(f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}")
    return " ".join(parts) or "меньше минуты"


def parse_period(text: str, today: date | None = None):
    """Текст -> (с, по, метка для ответа) или None. Год не сказан = текущий."""
    t = f" {text.lower().strip()} "
    today = today or date.today()
    if "позавчера" in t:
        d = today - timedelta(days=2)
        return d, d, "позавчера"
    if "вчера" in t:
        d = today - timedelta(days=1)
        return d, d, "вчера"
    match = _DATE_RE.search(t)  # раньше проверки «год»: в дате бывает «2025 года»
    if match:
        month = next(n for prefix, n in _MONTHS.items() if match.group(2).startswith(prefix))
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            d = date(year, month, int(match.group(1)))
        except ValueError:
            return None
        return d, d, "за " + re.sub(r"\s+", " ", match.group(0)).strip()
    match = _MONTH_RE.search(t)  # месяц по имени: «за июль [2025]»
    if match:
        month = _month_num(match.group(1))
        year = int(match.group(2)) if match.group(2) else today.year
        frm, to = _month_range(year, month)
        label = f"за {_MONTH_NAMES[month - 1]}" + (f" {year} года" if match.group(2) else "")
        return frm, to, label
    if "недел" in t:
        monday = today - timedelta(days=today.weekday())
        if "прошл" in t:
            return monday - timedelta(days=7), monday - timedelta(days=1), "за прошлую неделю"
        return monday, today, "за неделю"
    if "месяц" in t:
        if "прошл" in t:
            prev_last = today.replace(day=1) - timedelta(days=1)
            return prev_last.replace(day=1), prev_last, "за прошлый месяц"
        return today.replace(day=1), today, "за месяц"
    if "сегодня" in t:  # раньше «год»: в «сегодня» есть подстрока «год»
        return today, today, "сегодня"
    if "год" in t:
        if "прошл" in t:
            return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "за прошлый год"
        return date(today.year, 1, 1), today, "за год"
    return today, today, "сегодня"  # период не назван — значит, за сегодня


class WorktimeSkill(Skill):
    def __init__(self, b24: Bitrix24):
        self.b24 = b24

    def report(self, period_text: str) -> str:
        parsed = parse_period(period_text or "")
        if not parsed:
            return "Не понял период. Скажи: сегодня, вчера, за неделю, за месяц или дату"
        frm, to, label = parsed
        try:
            items = self.b24.elapsed(frm, to)
        except BitrixError as error:
            return f"Битрикс: {error}"

        by_task: dict[int, int] = {}
        days: set[str] = set()
        for item in items:
            seconds = int(item.get("SECONDS") or 0)
            task_id = int(item.get("TASK_ID") or 0)
            by_task[task_id] = by_task.get(task_id, 0) + seconds
            days.add(str(item.get("CREATED_DATE"))[:10])
        total = sum(by_task.values())
        if not total:
            return f"{label.capitalize()} записей времени нет"

        answer = f"{label.capitalize()} ты проработал {spoken_work(total)}"
        if (to - frm).days > 0 and len(days) > 1:
            answer += (f": {len(days)} {plural(len(days), 'рабочий день', 'рабочих дня', 'рабочих дней')}, "
                       f"в среднем {spoken_work(total // len(days))} в день")
        if len(by_task) > 1:
            top_id = max(by_task, key=by_task.get)
            try:
                title = self.b24.task_titles([top_id]).get(top_id)
            except BitrixError:
                title = None
            top = f"«{title}»" if title else f"задаче номер {top_id}"
            answer += f". Больше всего по {top} — {spoken_work(by_task[top_id])}"
        return answer

    def rules(self) -> list[Rule]:
        return [
            # период ищем во всей фразе: «сколько я ВЧЕРА проработал» и
            # «сколько я проработал ЗА НЕДЕЛЮ» устроены по-разному
            Rule(r"^сколько\s+(?:часов\s+|времени\s+)?(?:я\s+)?\S*\s*(?:про|от)?работал\w*(?:\s+.+)?$",
                 lambda m: self.report(m.group(0))),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("work_time",
                 "Сколько пользователь проработал (учёт времени в задачах Битрикс24)",
                 lambda a: self.report(a.get("period", "")),
                 params={"period": {"type": "string",
                                    "description": "Период: сегодня/вчера/за неделю/за месяц/дата («26 июня»)"}},
                 required=[], query_arg="period"),
        ]
