"""Фейки для тестов: Nuclear MCP с каноническими ответами, мозг, поиски."""

from __future__ import annotations

from src.services.nuclear import NuclearPlayer
from src.services.ollama import OllamaBrain


class FakeMcp:
    """Запоминает вызовы; ответы — из словаря canned (по имени метода)."""

    def __init__(self, canned: dict | None = None):
        self.calls: list[tuple[str, dict | None]] = []
        self.canned = canned or {}

    def call(self, method: str, params: dict | None = None):
        self.calls.append((method, params))
        value = self.canned.get(method)
        return value(params) if callable(value) else value

    def method_calls(self, method: str) -> list:
        return [p for m, p in self.calls if m == method]


def make_player(canned: dict | None = None) -> tuple[NuclearPlayer, FakeMcp]:
    mcp = FakeMcp(canned)
    return NuclearPlayer(mcp), mcp


class FakeBrain(OllamaBrain):
    """Отдаёт заранее заданные ответы вместо похода в Ollama."""

    def __init__(self, content: str = "", tool_calls: list | None = None,
                 chat_content: str = ""):
        super().__init__()
        self._content = content
        self._tool_calls = tool_calls or []
        self._chat_content = chat_content

    def raw(self, messages, tools=None):
        if tools is not None:
            return {"message": {"content": self._content, "tool_calls": self._tool_calls}}
        return {"message": {"content": self._chat_content}}


class FakeYoutubeSearch:
    def __init__(self, videos: list | None = None):
        self.videos = videos if videos is not None else [
            {"videoId": "vid1", "title": "Тестовый видос", "channel": "Канал", "durationMs": 60000},
        ]

    def search(self, query, limit=5):
        return self.videos


class FakeWeather:
    def get(self, city=""):
        return f"Сейчас в {city or 'городе'} плюс 20, ясно"


class FakeWeb:
    def __init__(self, results=None):
        self.results = results if results is not None else [("Заголовок", "Сниппет ответа")]

    def search(self, query, limit=5):
        return self.results
