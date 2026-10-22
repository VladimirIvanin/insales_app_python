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

from insales.resources.base import Resource


class Products(Resource):
    """Товары InSales."""

    _path = '/admin/products.json'

    def list(self, page: int = 1, per_page: int = 50,
             **filters: Any) -> list[dict[str, Any]]:
        """Получить список товаров."""
        params = [f'page={page}', f'per_page={per_page}']
        for key, value in filters.items():
            if value is not None:
                params.append(f'{key}={value}')
        path = f'{self._path}?{"&".join(params)}'
        resp = self._get(path)
        if not isinstance(resp.data, list):
            return []
        return resp.data

    def get(self, product_id: int) -> dict[str, Any] | None:
        """Получить товар по ID."""
        try:
            resp = self._get(self._make_path(f'{product_id}.json'))
            if isinstance(resp.data, dict):
                return resp.data
            return None
        except Exception:
            return None

    def count(self, **filters: Any) -> int:
        """Количество товаров."""
        params = []
        for key, value in filters.items():
            if value is not None:
                params.append(f'{key}={value}')
        path = '/admin/products/count.json'
        if params:
            path = f'{path}?{"&".join(params)}'
        try:
            resp = self._get(path)
            if isinstance(resp.data, dict):
                return resp.data.get('count', 0)
            return 0
        except Exception:
            return 0

    def update(self, product_id: int,
               data: dict[str, Any]) -> dict[str, Any] | None:
        """Обновить товар."""
        try:
            resp = self._put(self._make_path(f'{product_id}.json'), data)
            if isinstance(resp.data, dict):
                return resp.data
            return None
        except Exception:
            return None
