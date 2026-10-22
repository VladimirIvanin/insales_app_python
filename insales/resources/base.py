"""Базовый класс ресурса InSales API.

Каждый ресурс знает путь endpoint и использует транспорт для запросов.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from insales.response import ApiResponse
from insales.transport import Transport


class Resource:
    """Базовый ресурс API InSales.

    Подклассы определяют _collection_path и _object_path_template.
    """

    _collection_path: str = ''
    _object_path_template: str = ''

    def __init__(self, transport: Transport):
        self._transport = transport

    @property
    def _collection(self) -> str:
        """Полный путь к коллекции (например, /admin/orders.json)."""
        return self._collection_path

    def _object_path(self, obj_id: int) -> str:
        """Полный путь к объекту (например, /admin/orders/123.json).

        Формируется из _object_path_template или стандартного шаблона.
        """
        if self._object_path_template:
            return self._object_path_template.format(id=obj_id)
        if self._collection_path.endswith('.json'):
            base = self._collection_path[:-5]  # remove '.json'
        else:
            base = self._collection_path.rstrip('/')
        return f'{base}/{obj_id}.json'

    def _build_query(self, **params: Any) -> str:
        """Построить query string из параметров, пропуская None.

        Использует urllib.parse.urlencode для корректного кодирования
        дат, массивов и спецсимволов.
        """
        filtered = {k: v for k, v in params.items() if v is not None}
        if not filtered:
            return ''
        return '?' + urlencode(filtered, doseq=True)

    def _get(self, path: str) -> ApiResponse:
        return self._transport.get(path)

    def _post(self, path: str, data: Any = None) -> ApiResponse:
        return self._transport.post(path, data or {})

    def _put(self, path: str, data: Any) -> ApiResponse:
        return self._transport.put(path, data)

    def _delete(self, path: str) -> ApiResponse:
        return self._transport.delete(path)
