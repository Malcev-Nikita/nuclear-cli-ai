"""Bitrix24 REST по входящему вебхуку: учёт рабочего времени по задачам.

Вебхук содержит секретный токен: URL не печатать и не отдавать в тексты
ошибок (requests в своих исключениях показывает URL — здесь они глушатся).
"""

from __future__ import annotations

from datetime import date, timedelta

import requests

from src.config import B24_WEBHOOK, HTTP_TIMEOUT


class BitrixError(Exception):
    pass


class Bitrix24:
    def __init__(self, webhook: str = B24_WEBHOOK):
        self._base = webhook.rstrip("/") + "/"
        self._session = requests.Session()
        self._user_id: int | None = None

    def call(self, method: str, params: dict | None = None) -> dict:
        try:
            resp = self._session.post(self._base + method, json=params or {},
                                      timeout=HTTP_TIMEOUT)
            data = resp.json()
        except requests.RequestException as error:
            raise BitrixError(f"Битрикс не отвечает ({type(error).__name__})") from None
        except ValueError:
            raise BitrixError("Битрикс вернул не-JSON") from None
        if "error" in data:
            raise BitrixError(data.get("error_description") or data["error"])
        return data

    def user_id(self) -> int:
        """id владельца вебхука (кешируется на сессию)."""
        if self._user_id is None:
            self._user_id = int(self.call("profile")["result"]["ID"])
        return self._user_id

    def elapsed(self, frm: date, to: date) -> list[dict]:
        """Записи учёта времени за [frm, to] включительно: TASK_ID/SECONDS/CREATED_DATE."""
        params = {
            "ORDER": {"CREATED_DATE": "asc"},
            "FILTER": {
                "USER_ID": self.user_id(),
                ">=CREATED_DATE": frm.isoformat(),
                "<CREATED_DATE": (to + timedelta(days=1)).isoformat(),
            },
            "SELECT": ["ID", "TASK_ID", "SECONDS", "CREATED_DATE"],
        }
        # у старых методов task.* пагинация — PARAMS[NAV_PARAMS][iNumPage]
        items: list[dict] = []
        page = 1
        while True:
            data = self.call("task.elapseditem.getlist",
                             {**params, "PARAMS": {"NAV_PARAMS": {"iNumPage": page}}})
            chunk = data.get("result") or []
            items.extend(chunk)
            if not chunk or len(items) >= int(data.get("total") or 0):
                return items
            page += 1

    def task_titles(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        data = self.call("tasks.task.list",
                         {"filter": {"ID": ids}, "select": ["ID", "TITLE"]})
        return {int(t["id"]): t["title"] for t in data["result"].get("tasks", [])}
