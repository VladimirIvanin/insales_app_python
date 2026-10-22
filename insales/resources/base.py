"""Базовый класс ресурса InSales API.

Каждый ресурс знает путь endpoint и использует транспорт для запросов.
"""
from __future__ import annotations

from typing import Any

from insales.response import ApiResponse
from insales.transport import Transport


class Resource:
    """Базовый ресурс API InSales.

    Подклассы определяют _path и методы-обёртки.
    """

    _path: str = ''

    def __init__(self, transport: Transport):
        self._transport = transport

    def _make_path(self, *parts: str | int) -> str:
        """Собрать путь из базового пути и частей."""
        base = self._path.rstrip('/')
        if not parts:
            return base
        suffix = '/'.join(str(p) for p in parts)
        return f'{base}/{suffix}'

    def _get(self, path: str) -> ApiResponse:
        return self._transport.get(path)

    def _post(self, path: str, data: Any = None) -> ApiResponse:
        return self._transport.post(path, data or {})

    def _put(self, path: str, data: Any) -> ApiResponse:
        return self._transport.put(path, data)

    def _delete(self, path: str) -> ApiResponse:
        return self._transport.delete(path)
