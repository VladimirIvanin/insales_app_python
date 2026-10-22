"""Ресурс Products API InSales.

Поддержка:
- list    GET    /admin/products.json
- get     GET    /admin/products/{id}.json
- count   GET    /admin/products/count.json
- update  PUT    /admin/products/{id}.json

См. https://api.insales.ru/#product
"""
from __future__ import annotations

from typing import Any

from insales.errors import ResourceError
from insales.resources.base import Resource


class Products(Resource):
    """Товары InSales."""

    _collection_path = '/admin/products.json'
    _count_path = '/admin/products/count.json'

    def list(self, page: int = 1, per_page: int = 50,
             **filters: Any) -> list[dict[str, Any]]:
        """Получить список товаров.

        Returns:
            Список товаров.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        path = self._collection + self._build_query(
            page=page, per_page=per_page, **filters
        )
        resp = self._get(path)
        if not isinstance(resp.data, list):
            raise ResourceError(
                f'Expected list of products, got {type(resp.data).__name__}'
            )
        return resp.data

    def get(self, product_id: int) -> dict[str, Any]:
        """Получить товар по ID.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        resp = self._get(self._object_path(product_id))
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected product object, got {type(resp.data).__name__}'
            )
        return resp.data

    def count(self, **filters: Any) -> int:
        """Количество товаров.

        Returns:
            Число товаров.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        path = self._count_path + self._build_query(**filters)
        resp = self._get(path)
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected count object, got {type(resp.data).__name__}'
            )
        count = resp.data.get('count')
        if count is None or not isinstance(count, (int, float)):
            raise ResourceError(
                f'Missing or invalid "count" field in response: {resp.data!r}'
            )
        return int(count)

    def update(self, product_id: int,
               data: dict[str, Any]) -> dict[str, Any]:
        """Обновить товар.

        Returns:
            Обновлённый товар.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        resp = self._put(self._object_path(product_id), data)
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected product object, got {type(resp.data).__name__}'
            )
        return resp.data
