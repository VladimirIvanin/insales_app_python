"""Ресурс Orders API InSales.

Поддержка:
- list   GET    /admin/orders.json
- get    GET    /admin/orders/{id}.json
- count  GET    /admin/orders/count.json

См. https://api.insales.ru/#order
"""
from __future__ import annotations

from typing import Any

from insales.resources.base import Resource


class Orders(Resource):
    """Заказы InSales."""

    _path = '/admin/orders.json'

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
        """
        params = [f'page={page}', f'per_page={per_page}']
        for key, value in filters.items():
            if value is not None:
                params.append(f'{key}={value}')
        path = f'{self._path}?{"&".join(params)}'
        resp = self._get(path)
        if not isinstance(resp.data, list):
            return []
        return resp.data

    def get(self, order_id: int) -> dict[str, Any] | None:
        """Получить заказ по ID."""
        try:
            resp = self._get(self._make_path(f'{order_id}.json'))
            if isinstance(resp.data, dict):
                return resp.data
            return None
        except Exception:
            return None

    def count(self, **filters: Any) -> int:
        """Количество заказов (с учётом фильтров)."""
        params = []
        for key, value in filters.items():
            if value is not None:
                params.append(f'{key}={value}')
        path = '/admin/orders/count.json'
        if params:
            path = f'{path}?{"&".join(params)}'
        try:
            resp = self._get(path)
            if isinstance(resp.data, dict):
                return resp.data.get('count', 0)
            return 0
        except Exception:
            return 0

    def update(self, order_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        """Обновить заказ.

        Args:
            order_id: ID заказа.
            data: Данные для обновления в формате {'order': {...}}.

        Returns:
            Обновлённый заказ или None.
        """
        try:
            resp = self._put(self._make_path(f'{order_id}.json'), data)
            if isinstance(resp.data, dict):
                return resp.data
            return None
        except Exception:
            return None
