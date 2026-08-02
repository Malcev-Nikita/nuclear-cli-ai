"""Агент: команда -> regex-роутер навыков -> (не поймали) LLM с инструментами."""

from __future__ import annotations

import json
import re
import time

from src.config import ASSISTANT_LORE
from src.services.ollama import OllamaBrain
from src.skills.base import Skill

# Уточнение к прошлой команде: «а за позавчера?», «а в Москве?».
_FOLLOW_UP = re.compile(r"^(?:а|и)\s+(?:что\s+|как\s+)?(.+?)\s*\??$", re.IGNORECASE)
FOLLOW_UP_MEMORY_SEC = 180.0  # дольше — уже другой разговор

SYSTEM_PROMPT = (
    "Ты — голосовой помощник музыкального плеера. На каждую команду пользователя "
    "вызови ровно один подходящий инструмент. Названия песен, исполнителей и плейлистов "
    "передавай так, как их произнёс пользователь, не переводя на другой язык. "
    "На вопрос о погоде вызови get_weather, о времени — get_time, о дате — "
    "get_date. На вопрос о фактах, людях, событиях или новостях вызови "
    "web_search. На «запиши…»/«запомни…» вызови save_note с текстом дословно. "
    "На «поставь таймер» — set_timer, «напомни …» — set_reminder, «разбуди …» — set_alarm. "
    "Если это просто болтовня — ответь одной короткой фразой без инструментов."
)


class Agent:
    def __init__(self, skills: list[Skill], brain: OllamaBrain, lore: str = ASSISTANT_LORE):
        self.brain = brain
        # Порядок навыков = приоритет правил роутера.
        self.router = [
            (re.compile(rule.pattern, re.IGNORECASE), rule.handler, skill)
            for skill in skills for rule in skill.rules()
        ]
        self._tool_owner = {t.name: skill for skill in skills for t in skill.tools()}
        self._context: tuple[Skill, float] | None = None  # кто отвечал последним
        all_tools = [tool for skill in skills for tool in skill.tools()]
        self.tool_impl = {tool.name: tool.impl for tool in all_tools}
        self._schemas = [tool.schema() for tool in all_tools]
        # Для починки вырожденных ответов qwen3:1.7b:
        self._query_arg = {t.name: t.query_arg for t in all_tools if t.query_arg}
        self._no_arg = {t.name for t in all_tools if not t.params}
        self.system = SYSTEM_PROMPT
        if "work_time" in self.tool_impl:  # навык опциональный (нужен вебхук B24)
            self.system += " На вопрос «сколько я проработал/отработал» вызови work_time."
        if lore:
            self.system += f" Твой характер и предыстория: {lore}"

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        for pattern, handler, skill in self.router:
            match = pattern.match(text)
            if match:
                self._remember(skill)
                return handler(match)
        answer = self._follow_up(text)
        if answer is not None:
            return answer
        return self._handle_with_llm(text)

    def _remember(self, skill: Skill | None) -> None:
        if skill is not None:
            self._context = (skill, time.monotonic())

    def _follow_up(self, text: str) -> str | None:
        """«а за позавчера?» -> тот же навык, что отвечал только что."""
        if not self._context:
            return None
        skill, when = self._context
        if time.monotonic() - when > FOLLOW_UP_MEMORY_SEC:
            self._context = None
            return None
        match = _FOLLOW_UP.match(text)
        if not match:
            return None
        answer = skill.follow_up(match.group(1))
        if answer is not None:
            self._remember(skill)
        return answer

    def _handle_with_llm(self, text: str) -> str:
        tool_calls, content = self.brain.decide(self.system, text, self._schemas)
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
            self._remember(self._tool_owner.get(name))
            results.append(impl(arguments))
        return "; ".join(r for r in results if r)

    def _degenerate_fallback(self, content: str, text: str) -> str | None:
        """qwen3:1.7b вместо вызова иногда отвечает голым именем инструмента
        («play_track»), именем с JSON-аргументами на следующей строке или эхом
        самой команды. Чиним все три случая."""
        # get_forecast\n{"city": "Астрахань", "when": "завтра"} (вживую 2026-08-02)
        with_args = re.match(r"^\s*([a-z_]\w*)\s*[\r\n]+\s*(\{.*\})\s*$",
                             content.strip(), re.DOTALL)
        if with_args and with_args.group(1) in self.tool_impl:
            try:
                arguments = json.loads(with_args.group(2))
            except json.JSONDecodeError:
                arguments = {}
            if isinstance(arguments, dict):
                name = with_args.group(1)
                self._remember(self._tool_owner.get(name))
                return self.tool_impl[name](arguments)
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
