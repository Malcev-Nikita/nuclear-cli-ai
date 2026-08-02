"""Приглушение музыки на время речи ассистента — как у Яндекс станции.

Побочная польза: тихая музыка = тихое эхо в микрофоне, значит barge-in
(«заткнись» поверх речи) срабатывает надёжнее и реже ложно.
"""

from __future__ import annotations

from contextlib import contextmanager


class Ducker:
    """Обёртка вокруг плеера: приглушить на время речи и вернуть как было.

    Плеер нужен только с методами state/volume_pct/set_volume_pct — так что
    в тестах подставляется фейковый MCP.
    """

    def __init__(self, player, level_pct: int):
        self.player = player
        self.level = max(0, min(100, int(level_pct)))

    @contextmanager
    def quiet(self):
        restore = self._duck()
        try:
            yield
        finally:
            if restore is not None:
                try:
                    self.player.set_volume_pct(restore)
                except Exception:
                    pass  # Nuclear закрыли посреди фразы — не роняем голосовой цикл

    def _duck(self) -> int | None:
        """Приглушить, вернуть громкость для восстановления (None — не трогали)."""
        if not self.level:
            return None
        try:
            if self.player.state().get("status") != "playing":
                return None  # музыка не играет — громкость не наша забота
            current = self.player.volume_pct()
            if current <= self.level:
                return None  # и так тихо
            self.player.set_volume_pct(self.level)
            return current
        except Exception:
            return None  # Nuclear недоступен — просто говорим поверх
