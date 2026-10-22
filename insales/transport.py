"""HTTP-транспорт для API InSales.

Отправляет HTTP-запросы с Basic-аутентификацией, разбирает ответ,
сохраняет заголовки лимитов. Не зависит от Flask, SQLite и очереди.
"""
from __future__ import annotations

import base64
import http.client
import json
import re
from typing import Any

from insales.errors import ApiError, ResourceError, ResponseTooLargeError, TransportError
from insales.response import ApiResponse

MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT = 10
_OK_STATUSES = {200, 201, 202, 204}

_LOOPBACK_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost'})

_IPV6_PATTERN = re.compile(
    r'^\[([a-fA-F0-9:]+)\](?::(\d{1,5}))?$',
)
_HOST_PATTERN = re.compile(
    r'^([a-z0-9][a-z0-9.\-]*)(?::(\d{1,5}))?$',
)


def validate_shop(shop: str) -> str:
    """Валидация домена магазина (базовая проверка формата).

    Поддерживает:
    - Обычные домены: my-shop.myinsales.ru
    - IPv4: 127.0.0.1, 127.0.0.1:8765
    - IPv6: ::1, [::1]:8765
    """
    shop = shop.lower().strip()
    m = _IPV6_PATTERN.match(shop) or _HOST_PATTERN.match(shop)
    if not m:
        raise ValueError(f'Invalid shop domain format: {shop}')
    return shop


def _parse_shop(shop: str) -> tuple[str, int | None]:
    """Разобрать shop на host и опциональный port.

    Поддерживает:
    - Обычные домены: my-shop.myinsales.ru
    - IPv4: 127.0.0.1, 127.0.0.1:8765
    - IPv6: ::1, [::1]:8765
    """
    raw = shop.lower().strip()
    m = _IPV6_PATTERN.match(raw)
    if m:
        host = m.group(1)
        port = int(m.group(2)) if m.group(2) else None
        return host, port
    m = _HOST_PATTERN.match(raw)
    if not m:
        raise ValueError(f'Invalid shop domain format: {shop}')
    host = m.group(1)
    port = int(m.group(2)) if m.group(2) else None
    return host, port


class Transport:
    """HTTP-транспорт для одного магазина InSales.

    Привязан к конкретному магазину (shop + credentials).
    Потокобезопасен — каждый запрос создаёт новое подключение.
    """

    def __init__(self, shop: str, app_id: str, password: str,
                 dev: bool = False):
        host, port = _parse_shop(shop)
        self.shop = host
        self.port = port
        self.app_id = app_id
        self.password = password
        self._basic = base64.b64encode(
            f'{app_id}:{password}'.encode()
        ).decode()
        self._dev = dev

    def _connect(self) -> http.client.HTTPConnection:
        kwargs: dict[str, Any] = {'timeout': REQUEST_TIMEOUT}
        if self.port:
            kwargs['port'] = self.port
        if self._dev and self.shop in _LOOPBACK_HOSTS:
            return http.client.HTTPConnection(self.shop, **kwargs)
        return http.client.HTTPSConnection(self.shop, **kwargs)

    def request(self, method: str, path: str,
                body: bytes | None = None,
                content_type: str | None = None) -> ApiResponse:
        """Выполнить HTTP-запрос к API InSales.

        Args:
            method: HTTP-метод (GET, POST, PUT, DELETE).
            path: Путь endpoint (например, /admin/webhooks.json).
            body: Тело запроса в байтах (для POST/PUT).
            content_type: Content-Type тела (по умолчанию application/json).

        Returns:
            ApiResponse с распарсенными данными.

        Raises:
            TransportError: При сетевых ошибках и таймаутах.
            ApiError: При неуспешном HTTP-статусе.
            ResponseTooLarge: При превышении MAX_RESPONSE_BYTES.
        """
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

            # S3: проверяем, что ответ полный (защита от раннего EOF)
            content_length = response.getheader('Content-Length')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if content_length is not None:
                try:
                    expected = int(content_length)
                    if len(raw) < expected and len(raw) <= MAX_RESPONSE_BYTES:
                        raise TransportError(
                            f'Incomplete response: read {len(raw)} of '
                            f'{expected} bytes (possible network truncation)',
                        )
                except (ValueError, TypeError):
                    pass

            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(
                    status=response.status,
                    body='Response exceeds maximum allowed size',
                    usage=response.getheader('API-Usage-Limit'),
                )

            resp_headers = dict(response.getheaders())
            usage = response.getheader('API-Usage-Limit')

            if response.status not in _OK_STATUSES:
                retry_after = response.getheader('Retry-After')
                raise ApiError(
                    status=response.status,
                    body=raw.decode('utf-8', errors='replace'),
                    usage=usage,
                    retry_after=retry_after,
                )

            if not raw.strip():
                data = None
            else:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ResourceError(
                        f'Failed to decode JSON response: {exc}',
                    ) from exc
                except UnicodeDecodeError as exc:
                    raise ResourceError(
                        f'Response contains invalid UTF-8 bytes: {exc}',
                    ) from exc

            return ApiResponse(
                data=data,
                status=response.status,
                usage=usage,
                retry_after=response.getheader('Retry-After'),
                headers=resp_headers,
            )
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(str(exc)) from exc
        finally:
            conn.close()

    def get(self, path: str) -> ApiResponse:
        return self.request('GET', path)

    def post(self, path: str, data: Any) -> ApiResponse:
        return self.request('POST', path,
                            body=json.dumps(data).encode(),
                            content_type='application/json')

    def put(self, path: str, data: Any) -> ApiResponse:
        return self.request('PUT', path,
                            body=json.dumps(data).encode(),
                            content_type='application/json')

    def delete(self, path: str) -> ApiResponse:
        return self.request('DELETE', path)

    def get_account_data(self) -> tuple[dict, str | None]:
        """Удобный метод: получить account.json и usage.

        Returns:
            (account_dict, usage_string)
        """
        resp = self.get('/admin/account.json')
        if not isinstance(resp.data, dict):
            raise ApiError(
                status=resp.status,
                body=f'Expected account object, got {type(resp.data).__name__}',
                usage=resp.usage,
            )
        return resp.data, resp.usage

    def verify_identity(self, expected_store_id: str) -> bool:
        """Проверить, что credentials соответствуют ожидаемому store_id."""
        try:
            account, _ = self.get_account_data()
            return str(account.get('id')) == str(expected_store_id)
        except (ApiError, TransportError, OSError, ValueError, TypeError):
            return False


class RetryStrategy:
    """Политика повторов для временных ошибок API.

    Учитывает Retry-After и ограничивает число попыток.
    """

    def __init__(self, max_retries: int = 3,
                 base_delay: float = 1.0,
                 max_delay: float = 60.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def get_delay(self, attempt: int,
                  retry_after: str | None = None) -> float:
        """Вычислить задержку перед повтором.

        Если указан Retry-After, используется его значение.
        Иначе — exponential backoff с jitter.
        """
        if retry_after is not None:
            return min(float(retry_after), self.max_delay)

        import secrets
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        return delay * (0.5 + secrets.randbelow(1000) / 2000.0)

    def should_retry(self, attempt: int, error: ApiError) -> bool:
        """Определить, стоит ли повторить запрос."""
        if attempt >= self.max_retries:
            return False
        return error.is_temporary
