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


def fmt_track(track: dict) -> str:
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    title = track.get("title", "?")
    return f"{artists} — {title}" if artists else title


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def spoken_duration(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        if minutes == 1:
            return "минуту"
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"
    return f"{seconds} {plural(seconds, 'секунду', 'секунды', 'секунд')}"
