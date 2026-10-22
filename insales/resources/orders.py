"""Ресурс Orders API InSales.

Поддержка:
- list   GET    /admin/orders.json
- get    GET    /admin/orders/{id}.json
- count  GET    /admin/orders/count.json

См. https://api.insales.ru/#order
"""
from __future__ import annotations

from typing import Any

from insales.errors import ResourceError
from insales.resources.base import Resource


class Orders(Resource):
    """Заказы InSales."""

    _collection_path = '/admin/orders.json'
    _count_path = '/admin/orders/count.json'

    def list(self, page: int = 1, per_page: int = 50,
             **filters: Any) -> list[dict[str, Any]]:
        """Получить список заказов.

        Args:
            page: Номер страницы.
            per_page: Элементов на странице (макс. 200).
            filters: Дополнительные параметры фильтрации
                     (created_at_from, status, и т.д.).

        Returns:
            Список заказов.

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
                f'Expected list of orders, got {type(resp.data).__name__}'
            )
        return resp.data

    def get(self, order_id: int) -> dict[str, Any]:
        """Получить заказ по ID.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        resp = self._get(self._object_path(order_id))
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected order object, got {type(resp.data).__name__}'
            )
        return resp.data

    def count(self, **filters: Any) -> int:
        """Количество заказов (с учётом фильтров).

        Returns:
            Число заказов.

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

    def update(self, order_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """Обновить заказ.

        Args:
            order_id: ID заказа.
            data: Данные для обновления в формате {'order': {...}}.

        Returns:
            Обновлённый заказ.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        resp = self._put(self._object_path(order_id), data)
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected order object, got {type(resp.data).__name__}'
            )
        return resp.data
