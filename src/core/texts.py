"""Общие текстовые помощники: склонения, форматирование «под озвучку»."""

from __future__ import annotations


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def spoken_clock(hour: int, minute: int = 0) -> str:
    """«7 часов», «14 часов 35 минут» — piper так читает надёжнее, чем «14:35»."""
    hours = f"{hour} {plural(hour, 'час', 'часа', 'часов')}"
    if not minute:
        return hours
    return f"{hours} {minute} {plural(minute, 'минута', 'минуты', 'минут')}"


def fmt_track(track: dict) -> str:
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    title = track.get("title", "?")
    return f"{artists} — {title}" if artists else title


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def spoken_duration(seconds: int) -> str:
    """«30 секунд», «минуту», «час 30 минут», «2 часа» — как говорят вслух."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} {plural(seconds, 'секунду', 'секунды', 'секунд')}"
    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append("час" if hours == 1 else f"{hours} {plural(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append("минуту" if minutes == 1
                     else f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}")
    if rest and not hours:  # к часам секунды не приписываем — не для озвучки
        parts.append(f"{rest} {plural(rest, 'секунду', 'секунды', 'секунд')}")
    return " ".join(parts)
