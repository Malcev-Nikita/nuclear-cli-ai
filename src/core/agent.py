"""Агент: команда -> regex-роутер навыков -> (не поймали) LLM с инструментами."""

from __future__ import annotations

import json
import re

from src.services.ollama import OllamaBrain
from src.skills.base import Skill

SYSTEM_PROMPT = (
    "Ты — голосовой помощник музыкального плеера. На каждую команду пользователя "
    "вызови ровно один подходящий инструмент. Названия песен, исполнителей и плейлистов "
    "передавай так, как их произнёс пользователь, не переводя на другой язык. "
    "На вопрос о погоде вызови get_weather, о времени — get_time, о дате — "
    "get_date. На вопрос о фактах, людях, событиях или новостях вызови "
    "web_search. Если это просто болтовня — ответь одной короткой фразой "
    "без инструментов."
)


class Agent:
    def __init__(self, skills: list[Skill], brain: OllamaBrain):
        self.brain = brain
        # Порядок навыков = приоритет правил роутера.
        self.router = [
            (re.compile(rule.pattern, re.IGNORECASE), rule.handler)
            for skill in skills for rule in skill.rules()
        ]
        all_tools = [tool for skill in skills for tool in skill.tools()]
        self.tool_impl = {tool.name: tool.impl for tool in all_tools}
        self._schemas = [tool.schema() for tool in all_tools]
        # Для починки вырожденных ответов qwen3:1.7b:
        self._query_arg = {t.name: t.query_arg for t in all_tools if t.query_arg}
        self._no_arg = {t.name for t in all_tools if not t.params}

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        for pattern, handler in self.router:
            match = pattern.match(text)
            if match:
                return handler(match)
        return self._handle_with_llm(text)

    def _handle_with_llm(self, text: str) -> str:
        tool_calls, content = self.brain.decide(SYSTEM_PROMPT, text, self._schemas)
        if not tool_calls:
            fallback = self._degenerate_fallback(content, text)
            if fallback is not None:
                return fallback
            return content or "Не понял команду"

        results = []
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            impl = self.tool_impl.get(name)
            if not impl:
                results.append(f"Неизвестный инструмент: {name}")
                continue
            results.append(impl(arguments))
        return "; ".join(r for r in results if r)

    def _degenerate_fallback(self, content: str, text: str) -> str | None:
        """qwen3:1.7b вместо вызова иногда отвечает голым именем инструмента
        («play_track») или эхом самой команды. Чиним оба случая."""
        tool_name = content.strip().strip('«»"`.,! ').lower()
        subject = re.sub(r"^(?:включ\w+|поставь|запусти|сыграй)\s+", "",
                         text.strip(), flags=re.IGNORECASE)
        if tool_name in self._no_arg and tool_name in self.tool_impl:
            return self.tool_impl[tool_name]({})
        if tool_name in self._query_arg and subject:
            return self.tool_impl[tool_name]({self._query_arg[tool_name]: subject})
        norm = lambda s: re.sub(r"[^\wё ]", "", s.lower()).strip()
        if norm(content) == norm(text) and subject != text.strip():
            play = self.tool_impl.get("play_track")  # эхо «включи X» -> играем X
            if play:
                return play({"query": subject})
        return None
