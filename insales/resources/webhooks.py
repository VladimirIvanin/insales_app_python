"""Ресурс Webhooks API InSales.

Поддержка:
- list   GET    /admin/webhooks.json
- get    GET    /admin/webhooks/{id}.json
- create POST   /admin/webhooks.json
- update PUT    /admin/webhooks/{id}.json
- delete DELETE /admin/webhooks/{id}.json

См. https://api.insales.ru/#webhook
"""
from __future__ import annotations

from typing import Any

from insales.errors import ResourceError
from insales.resources.base import Resource


class Webhooks(Resource):
    """Вебхуки InSales."""

    _collection_path = '/admin/webhooks.json'

    def list(self, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """Получить список вебхуков.

        Returns:
            Список вебхуков.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        path = self._collection + self._build_query(page=page, per_page=per_page)
        resp = self._get(path)
        if not isinstance(resp.data, list):
            raise ResourceError(
                f'Expected list of webhooks, got {type(resp.data).__name__}'
            )
        return resp.data

    def get(self, webhook_id: int) -> dict[str, Any]:
        """Получить вебхук по ID.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        resp = self._get(self._object_path(webhook_id))
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected webhook object, got {type(resp.data).__name__}'
            )
        return resp.data

    def create(self, topic: str, address: str,
               format_type: str = 'json',
               **extra: Any) -> dict[str, Any]:
        """Создать вебхук.

        Args:
            topic: Тема события (например, 'orders/update').
            address: URL для уведомлений.
            format_type: Формат данных ('json' или 'xml').
            extra: Дополнительные поля (например, include_fields).

        Returns:
            Созданный вебхук.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        data: dict[str, Any] = {
            'webhook': {
                'topic': topic,
                'address': address,
                'format_type': format_type,
                **extra,
            },
        }
        resp = self._post(self._collection, data)
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected webhook object, got {type(resp.data).__name__}'
            )
        return resp.data

    def update(self, webhook_id: int,
               topic: str | None = None,
               address: str | None = None,
               format_type: str | None = None,
               **extra: Any) -> dict[str, Any]:
        """Обновить вебхук.

        Передаются только поля, которые нужно изменить.

        Returns:
            Обновлённый вебхук.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
            ResourceError: При неожиданном формате ответа.
        """
        wh: dict[str, Any] = {}
        if topic is not None:
            wh['topic'] = topic
        if address is not None:
            wh['address'] = address
        if format_type is not None:
            wh['format_type'] = format_type
        wh.update(extra)

        data: dict[str, Any] = {'webhook': wh}
        resp = self._put(self._object_path(webhook_id), data)
        if not isinstance(resp.data, dict):
            raise ResourceError(
                f'Expected webhook object, got {type(resp.data).__name__}'
            )
        return resp.data

    def delete(self, webhook_id: int) -> None:
        """Удалить вебхук по ID.

        Raises:
            ApiError: При неуспешном HTTP-статусе.
            TransportError: При сетевых ошибках.
        """
        self._delete(self._object_path(webhook_id))
