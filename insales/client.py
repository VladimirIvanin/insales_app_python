"""HTTP-клиент для API InSales.

Поддерживает Basic-аутентификацию (app_id + store_password),
ограничение размера ответа, таймауты, отказ от следования редиректам
и заголовки лимитов API.

Основание: generated-api спецификации InSales, контракты из lifecycle.md.
"""
import base64
import http.client
import json
import re
from typing import Any

MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT = 10


class ApiError(Exception):
    """Ошибка API InSales."""

    def __init__(self, status: int, body: str = '',
                 usage: str | None = None,
                 retry_after: str | None = None):
        self.status = status
        self.body = body
        self.usage = usage
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [f'HTTP {self.status}']
        if self.usage:
            parts.append(f'usage={self.usage}')
        if self.retry_after:
            parts.append(f'retry_after={self.retry_after}')
        return ' | '.join(parts)


def validate_shop(shop: str) -> str:
    """Валидация домена магазина (базовая проверка формата)."""
    shop = shop.lower().strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9.\-]*(?::\d{1,5})?', shop):
        raise ValueError(f'Invalid shop domain format: {shop}')
    return shop


class InSalesClient:
    """Клиент InSales API для одного магазина."""

    def __init__(self, shop: str, app_id: str, password: str,
                 dev: bool = False):
        self.shop = validate_shop(shop)
        self.app_id = app_id
        self.password = password
        self._basic = base64.b64encode(
            f'{app_id}:{password}'.encode()
        ).decode()
        self._dev = dev

    def _connect(self) -> http.client.HTTPConnection:
        if self._dev:
            return http.client.HTTPConnection(self.shop, timeout=REQUEST_TIMEOUT)
        return http.client.HTTPSConnection(self.shop, timeout=REQUEST_TIMEOUT)

    def _request(self, method: str, path: str,
                 body: bytes | None = None,
                 content_type: str | None = None) -> tuple[Any, str | None]:
        """Выполнить запрос к API. Не следует редиректам."""
        headers = {
            'Authorization': f'Basic {self._basic}',
            'Accept': 'application/json',
        }
        if body is not None:
            headers['Content-Type'] = content_type or 'application/json'

        conn = self._connect()
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('API response too large')
            usage = response.getheader('API-Usage-Limit')
            retry_after = response.getheader('Retry-After')

            if response.status != 200:
                raise ApiError(
                    status=response.status,
                    body=raw.decode('utf-8', errors='replace'),
                    usage=usage,
                    retry_after=retry_after,
                )

            return json.loads(raw), usage
        finally:
            conn.close()

    def get(self, path: str) -> tuple[Any, str | None]:
        """GET-запрос к InSales API."""
        return self._request('GET', path)

    def post(self, path: str, data: Any) -> tuple[Any, str | None]:
        """POST-запрос с JSON-телом."""
        return self._request('POST', path,
                             body=json.dumps(data).encode(),
                             content_type='application/json')

    def put(self, path: str, data: Any) -> tuple[Any, str | None]:
        """PUT-запрос с JSON-телом."""
        return self._request('PUT', path,
                             body=json.dumps(data).encode(),
                             content_type='application/json')

    def delete(self, path: str) -> tuple[Any, str | None]:
        """DELETE-запрос."""
        return self._request('DELETE', path)

    def get_account(self) -> tuple[dict, str | None]:
        """Получить информацию о магазине (/admin/account.json)."""
        result, usage = self.get('/admin/account.json')
        return result, usage

    def verify_identity(self, expected_store_id: str) -> bool:
        """Проверить, что магазин с данным ID возвращается account API."""
        try:
            account, _ = self.get_account()
            return str(account.get('id')) == str(expected_store_id)
        except (ApiError, OSError, ValueError):
            return False
