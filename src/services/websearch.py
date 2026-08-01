"""Поиск DuckDuckGo (HTML-эндпоинт, без ключа). При смене разметки — чинить тут."""

from __future__ import annotations

import html
import re

import requests


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


class DuckDuckGo:
    def search(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        """-> [(заголовок, сниппет)]"""
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "ru-ru"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        results = []
        for i, title in enumerate(titles[:limit]):
            snippet = snippets[i] if i < len(snippets) else ""
            results.append((_strip_html(title), _strip_html(snippet)))
        return results
