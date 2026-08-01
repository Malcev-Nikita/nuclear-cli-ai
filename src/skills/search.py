"""Поиск в интернете + краткий пересказ моделью.

Единственное место с двумя вызовами LLM на команду: без второго круга
пользователю пришлось бы слушать сырые сниппеты поисковика.
"""

from __future__ import annotations

import requests

from src.services.ollama import OllamaBrain
from src.services.websearch import DuckDuckGo
from src.skills.base import Rule, Skill, Tool


class SearchSkill(Skill):
    def __init__(self, web: DuckDuckGo, brain: OllamaBrain):
        self.web = web
        self.brain = brain

    def answer(self, query: str) -> str:
        try:
            results = self.web.search(query)
        except requests.RequestException as error:
            return f"Поиск не удался: {error}"
        if not results:
            return f"По запросу «{query}» ничего не нашлось"
        context = "\n".join(f"- {title}. {snippet}" for title, snippet in results)
        try:
            content = self.brain.chat([
                {"role": "system", "content":
                    "Ответь на вопрос пользователя по результатам поиска: кратко, "
                    "1-3 предложения, по-русски, без ссылок и лишних слов — ответ "
                    "будет озвучен голосом. Отвечай сразу, без рассуждений."},
                # /no_think — софт-переключатель qwen3, глушит рассуждения
                {"role": "user",
                 "content": f"Вопрос: {query}\n\nРезультаты поиска:\n{context} /no_think"},
            ])
            if content:
                return content
        except requests.RequestException:
            pass
        title, snippet = results[0]  # LLM недоступна — хотя бы первый сниппет
        return snippet or title

    def rules(self) -> list[Rule]:
        return [
            Rule(r"^(?:найди|загугли|погугли|поищи)\s+(.+)$", lambda m: self.answer(m.group(1))),
        ]

    def tools(self) -> list[Tool]:
        return [
            Tool("web_search", "Найти в интернете ответ на вопрос о фактах, людях, событиях, новостях",
                 lambda a: self.answer(a["query"]),
                 params={"query": {"type": "string", "description": "Поисковый запрос"}},
                 required=["query"], query_arg="query"),
        ]
