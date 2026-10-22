"""Проверка и приём webhook-уведомлений от InSales.

Последовательность приёма:
1. Найти подписку по секрету из ?token=
2. Проверить размер, Content-Type и JSON
3. Сохранить событие в inbox
4. Зафиксировать транзакцию
"""
from __future__ import annotations

import json

from app.repositories.webhook_inbox import WebhookInbox
from app.repositories.webhook_subscriptions import WebhookSubscriptions

MAX_PAYLOAD_BYTES = 1_000_000


class WebhookReceiver:
    """Приёмщик webhook-уведомлений от InSales."""

    def __init__(self, subscriptions: WebhookSubscriptions,
                 inbox: WebhookInbox):
        self._subs = subscriptions
        self._inbox = inbox

    def receive(self, token: str, body: bytes,
                headers: dict[str, str] | None = None) -> WebhookResult:
        """Принять и сохранить webhook-уведомление.

        Args:
            token: Секрет подписки из query-параметра ?token=.
            body: Тело запроса (сырые байты).
            headers: Заголовки HTTP-запроса.

        Returns:
            WebhookResult с результатом обработки.
        """
        # Шаг 1: проверить размер
        if len(body) > MAX_PAYLOAD_BYTES:
            return WebhookResult(
                accepted=False,
                status=413,
                error='Payload too large',
            )

        # Шаг 2: найти подписку
        sub = self._subs.get_by_secret(token)
        if sub is None:
            return WebhookResult(
                accepted=False,
                status=404,
                error='Unknown subscription token',
            )

        if sub.get('state') != 'active':
            return WebhookResult(
                accepted=False,
                status=404,
                error='Subscription is not active',
            )

        # Шаг 3: проверить Content-Type и распарсить JSON
        content_type = (headers or {}).get('Content-Type', '')
        if 'application/json' not in content_type and content_type:
            return WebhookResult(
                accepted=False,
                status=415,
                error=f'Unsupported Content-Type: {content_type}',
            )

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return WebhookResult(
                accepted=False,
                status=400,
                error=f'Invalid JSON: {exc}',
            )

        # Шаг 4: определить тему
        # InSales не включает тему в тело уведомления — она известна из подписки
        topic = sub['topic']

        # Шаг 5: сохранить в inbox
        inbox_id = self._inbox.push(
            store_id=sub['store_id'],
            generation=sub['generation'],
            topic=topic,
            payload=payload,
            headers=headers,
        )

        return WebhookResult(
            accepted=True,
            status=200,
            inbox_id=inbox_id,
            topic=topic,
            store_id=sub['store_id'],
        )


class WebhookResult:
    """Результат приёма webhook-уведомления."""

    def __init__(self, accepted: bool, status: int,
                 error: str | None = None,
                 inbox_id: int | None = None,
                 topic: str | None = None,
                 store_id: str | None = None):
        self.accepted = accepted
        self.status = status
        self.error = error
        self.inbox_id = inbox_id
        self.topic = topic
        self.store_id = store_id
