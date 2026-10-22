"""Ресурс Account API InSales.

GET /admin/account.json
"""
from __future__ import annotations

from typing import Any

from insales.resources.base import Resource


class Account(Resource):
    """Аккаунт (магазин) InSales."""

    _path = '/admin/account.json'

    def get(self) -> dict[str, Any]:
        """Получить данные аккаунта текущего магазина."""
        resp = self._get(self._path)
        if not isinstance(resp.data, dict):
            raise TypeError(
                f'Expected account object, got {type(resp.data).__name__}'
            )
        return resp.data
