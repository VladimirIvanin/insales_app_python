"""Модель ответа API InSales.

Содержит данные, HTTP-статус и заголовки лимитов.
"""
from __future__ import annotations

from typing import Any


class ApiResponse:
    """Ответ API InSales.

    Атрибуты:
        data: Распарсенные данные (dict, list или None для пустых ответов).
        status: HTTP-статус ответа.
        usage: Значение заголовка API-Usage-Limit (или None).
        retry_after: Значение заголовка Retry-After в секундах (или None).
        headers: Сырые заголовки ответа (dict[str, str]).
    """

    def __init__(self, data: Any, status: int = 200,
                 usage: str | None = None,
                 retry_after: str | None = None,
                 headers: dict[str, str] | None = None):
        self.data = data
        self.status = status
        self.usage = usage
        self.retry_after = retry_after
        self.headers = headers or {}

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    @property
    def is_empty(self) -> bool:
        return self.data is None

    def __repr__(self) -> str:
        return (f'ApiResponse(status={self.status}, '
                f'data_type={type(self.data).__name__}, '
                f'usage={self.usage!r})')
