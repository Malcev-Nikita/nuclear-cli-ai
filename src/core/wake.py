"""Wake-логика: имя ассистента, команды без имени, фильтр галлюцинаций."""

from __future__ import annotations

import re

from config import (
    ASSISTANT_NAMES,
    BARE_COMMANDS,
    CONTEXT_COMMAND_MAX_WORDS,
    SHUTUP_COMMANDS,
)

# Типичные галлюцинации whisper на шуме/музыке.
_JUNK = re.compile(
    r"субтитр|dimatorzok|редактор|продолжение следует|спасибо за просмотр",
    re.IGNORECASE,
)


def normalize_words(text: str) -> list[str]:
    return re.sub(r"[^\wё]+", " ", text.lower().replace("ё", "е")).split()


def looks_like_junk(text: str) -> bool:
    return not re.search(r"[а-яa-z]", text.lower().replace("ё", "е")) or bool(_JUNK.search(text))


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


class WakeMatcher:
    """Решает, обращались ли к ассистенту, и достаёт команду из фразы."""

    def __init__(self, names: list[str] | None = None,
                 bare: re.Pattern = BARE_COMMANDS,
                 shutup: re.Pattern = SHUTUP_COMMANDS,
                 context_max_words: int = CONTEXT_COMMAND_MAX_WORDS):
        self.names = names if names is not None else ASSISTANT_NAMES
        self.bare = bare
        self.shutup = shutup
        self.context_max_words = context_max_words

    def is_name(self, word: str) -> bool:
        # Нечётко (±1 буква): «Мака»/«Магу» тоже имя; побочно ловятся падежи.
        for name in self.names:
            max_dist = 1 if len(name) >= 4 else 0
            if _levenshtein(word, name) <= max_dist:
                return True
        return False

    def extract_command(self, text: str) -> str | None:
        """Имя ищется в любом месте (VAD склеивает предложения).

        -> команда после имени (последнее вхождение с продолжением);
        "" если фраза — только имя; None, если имени нет.
        """
        words = normalize_words(text)
        hits = [i for i, word in enumerate(words) if self.is_name(word)]
        if not hits:
            return None
        for i in reversed(hits):
            tail = words[i + 1:]
            if tail:
                return " ".join(tail)
        return ""

    def control_in_context(self, words: list[str]) -> str | None:
        """Управляющая команда первым/последним словом короткой фразы:
        «…этим приложением, продолжай» -> «продолжай». Длинные фразы не трогаем."""
        if not words or not self.context_max_words or len(words) > self.context_max_words:
            return None
        for candidate in (words[-1], words[0]):
            if self.bare.match(candidate) or self.shutup.match(candidate):
                return candidate
        return None

    def has_shutup_word(self, words: list[str]) -> bool:
        return any(self.shutup.match(w) for w in words)

    def has_name(self, words: list[str]) -> bool:
        return any(self.is_name(w) for w in words)
