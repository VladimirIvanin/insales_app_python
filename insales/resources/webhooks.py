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

from insales.resources.base import Resource


class Webhooks(Resource):
    """Вебхуки InSales."""

    _path = '/admin/webhooks.json'

    def list(self, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """Получить список вебхуков."""
        path = f'{self._path}?page={page}&per_page={per_page}'
        resp = self._get(path)
        if not isinstance(resp.data, list):
            return []
        return resp.data

    def get(self, webhook_id: int) -> dict[str, Any] | None:
        """Получить вебхук по ID."""
        try:
            resp = self._get(self._make_path(webhook_id))
            if isinstance(resp.data, dict):
                return resp.data
            return None
        except Exception:
            return None

    def create(self, topic: str, address: str,
               format_type: str = 'json',
               **extra: Any) -> dict[str, Any] | None:
        """Создать вебхук.

        Args:
            topic: Тема события (например, 'orders/update').
            address: URL для уведомлений.
            format_type: Формат данных ('json' или 'xml').
            extra: Дополнительные поля (например, include_fields).

        Returns:
            Созданный вебхук или None при ошибке.
        """
        data: dict[str, Any] = {
            'webhook': {
                'topic': topic,
                'address': address,
                'format_type': format_type,
                **extra,
            },
        }
        resp = self._post(self._path, data)
        if isinstance(resp.data, dict):
            return resp.data
        return None

    def update(self, webhook_id: int,
               topic: str | None = None,
               address: str | None = None,
               format_type: str | None = None,
               **extra: Any) -> dict[str, Any] | None:
        """Обновить вебхук.

        Передаются только поля, которые нужно изменить.
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
        resp = self._put(self._make_path(webhook_id), data)
        if isinstance(resp.data, dict):
            return resp.data
        return None

    def delete(self, webhook_id: int) -> bool:
        """Удалить вебхук по ID.

        Returns:
            True, если удаление успешно (204 или 200).
        """
        try:
            self._delete(self._make_path(webhook_id))
            return True
        except Exception:
            return False
