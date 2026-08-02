"""Ollama-«мозг» + вся дипломатия с qwen3 (think-теги, кривые tool call'ы)."""

from __future__ import annotations

import json
import re
import threading

import requests

from src.config import HTTP_TIMEOUT, OLLAMA_KEEP_ALIVE, OLLAMA_MODEL, OLLAMA_URL


def strip_think(text: str) -> str:
    """Вырезать рассуждения qwen3 — даже одинокие/незакрытые теги."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:  # рассуждения без открывающего тега
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:  # открыли и не закрыли — ответа не было
        text = text.split("<think>", 1)[0]
    return text.strip()


def parse_text_tool_call(content: str) -> dict | None:
    """{"name": ..., "arguments": {...}} текстом -> формат tool_calls."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "name" not in obj:
        return None
    arguments = obj.get("arguments") or obj.get("parameters") or {}
    return {"function": {"name": obj["name"], "arguments": arguments}}


class OllamaBrain:
    def __init__(self, url: str = OLLAMA_URL, model: str = OLLAMA_MODEL,
                 keep_alive: str = OLLAMA_KEEP_ALIVE, timeout: int = HTTP_TIMEOUT):
        self.url = url
        self.model = model
        self.keep_alive = keep_alive
        self.timeout = timeout
        self._think_supported = True
        self._http = requests.Session()

    def raw(self, messages: list[dict], tools: list | None = None) -> dict:
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "messages": messages,
            "options": {"temperature": 0},
        }
        if tools:
            body["tools"] = tools
        if self._think_supported:
            body["think"] = False  # рассуждения qwen3 = +секунды латентности
        resp = self._http.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        if resp.status_code == 400 and self._think_supported and "think" in resp.text.lower():
            self._think_supported = False  # старые Ollama не знают параметр
            return self.raw(messages, tools)
        resp.raise_for_status()
        return resp.json()

    def decide(self, system: str, user: str, tools: list) -> tuple[list, str]:
        """Выбор инструмента: -> (tool_calls, текст-ответ, если вызовов нет)."""
        reply = self.raw(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=tools,
        )
        message = reply.get("message", {})
        tool_calls = message.get("tool_calls") or []
        content = strip_think(message.get("content") or "")
        if not tool_calls:
            text_call = parse_text_tool_call(content)  # tool call текстом
            if text_call:
                return [text_call], ""
        return tool_calls, content

    def chat(self, messages: list[dict]) -> str:
        reply = self.raw(messages)
        return strip_think(reply.get("message", {}).get("content") or "")

    def warmup_async(self) -> None:
        """Грузим модель в память в фоне — первая команда без холодного старта."""
        def _load():
            try:
                self._http.post(
                    f"{self.url}/api/chat",
                    json={"model": self.model, "messages": [], "keep_alive": self.keep_alive},
                    timeout=self.timeout,
                )
            except requests.RequestException:
                pass  # недоступность всплывёт с нормальной ошибкой на первой команде
        threading.Thread(target=_load, daemon=True).start()

    def version(self) -> str:
        resp = self._http.get(f"{self.url}/api/version", timeout=5)
        return resp.json().get("version", "?")
