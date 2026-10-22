"""HTTP-клиент для API InSales.

Фасад над Transport + Resource. Собирает ресурсы в атрибуты:
    client.account.get()
    client.orders.get(order_id)
"""
from __future__ import annotations

from typing import Any

# Совместимость: реэкспорт для старых импортов
from insales.errors import ApiError  # noqa: F401
from insales.resources.account import Account
from insales.resources.orders import Orders
from insales.resources.products import Products
from insales.transport import (  # noqa: F401
    MAX_RESPONSE_BYTES,  # noqa: F401
    Transport,
    validate_shop,
)


class InSalesClient:
    """Клиент InSales API для одного магазина.

    Использование:
        client = InSalesClient(shop='shop.example',
                                app_id='app', password='pwd')
        account = client.account.get()
    """

    def __init__(self, shop: str, app_id: str, password: str,
                 dev: bool = False):
        self._transport = Transport(shop=shop, app_id=app_id,
                                    password=password, dev=dev)

        # Ресурсы
        self.account = Account(self._transport)
        self.orders = Orders(self._transport)
        self.products = Products(self._transport)

    # ---- Совместимость со старым API ----

    @property
    def shop(self) -> str:
        return self._transport.shop

    @property
    def app_id(self) -> str:
        return self._transport.app_id

    @property
    def password(self) -> str:
        return self._transport.password

    def get(self, path: str) -> tuple[Any, str | None]:
        """(legacy) GET-запрос, возвращает (data, usage)."""
        resp = self._transport.get(path)
        return resp.data, resp.usage

    def post(self, path: str, data: Any) -> tuple[Any, str | None]:
        """(legacy) POST-запрос, возвращает (data, usage)."""
        resp = self._transport.post(path, data)
        return resp.data, resp.usage

    def put(self, path: str, data: Any) -> tuple[Any, str | None]:
        """(legacy) PUT-запрос, возвращает (data, usage)."""
        resp = self._transport.put(path, data)
        return resp.data, resp.usage

    def delete(self, path: str) -> tuple[Any, str | None]:
        """(legacy) DELETE-запрос, возвращает (data, usage)."""
        resp = self._transport.delete(path)
        return resp.data, resp.usage

    def get_account(self) -> tuple[dict, str | None]:
        """(legacy) Получить данные аккаунта магазина."""
        return self._transport.get_account_data()

    def verify_identity(self, expected_store_id: str) -> bool:
        """(legacy) Проверить, что credentials соответствуют store_id."""
        return self._transport.verify_identity(expected_store_id)
