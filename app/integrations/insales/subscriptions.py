"""Управление webhook-подписками магазина.

Регистрация подписок после подтверждённой активации магазина.
Сначала сохраняется локальная подписка, затем создаётся удалённая
через API InSales.
"""
from __future__ import annotations

from app.repositories.webhook_subscriptions import (
    SubscriptionRow,
    WebhookSubscriptions,
)
from insales.client import InSalesClient
from insales.errors import ApiError, TransportError
from insales.transport import RetryStrategy


class SubscriptionService:
    """Бизнес-логика управления webhook-подписками."""

    # Темы, на которые приложение подписывается по умолчанию
    # Допустимые темы InSales: orders/create, orders/update, orders/destroy,
    # products/create, products/update, client/create, client/update
    DEFAULT_TOPICS = [
        'orders/create',
        'orders/update',
        'orders/destroy',
        'products/create',
        'products/update',
        'client/create',
        'client/update',
    ]

    def __init__(self, subscriptions: WebhookSubscriptions,
                 base_url: str):
        self._subs = subscriptions
        self._base_url = base_url
        self._retry = RetryStrategy(max_retries=3)

    def register_defaults(self, client: InSalesClient,
                           store_id: str, generation: int) -> list[SubscriptionRow]:
        """Зарегистрировать стандартный набор подписок.

        Пропускает темы, для которых уже есть активная подписка
        текущего поколения **с подтверждённым insales_id** (устойчив
        к повторным вызовам).

        Подписка со state='active' и NULL insales_id считается
        неподтверждённой — после сбоя API она будет повторно создана.

        1. Создать локальную подписку.
        2. Создать удалённую через API.
        3. Сохранить insales_id.

        Returns:
            Список созданных подписок.
        """
        results: list[SubscriptionRow] = []
        for topic in self.DEFAULT_TOPICS:
            existing = self._subs.get_by_store_and_topic(store_id, topic)
            # Пропускаем только полностью завершённые подписки
            if existing is not None and existing['state'] == 'active':
                if existing.get('generation', 0) == generation:
                    if existing.get('insales_id') is not None:
                        continue

            sub = self.register_topic(client, store_id, generation, topic)
            if sub:
                results.append(sub)
        return results

    def register_topic(self, client: InSalesClient,
                        store_id: str, generation: int,
                        topic: str) -> SubscriptionRow | None:
        """Зарегистрировать подписку на одну тему.

        Порядок:
        1. Сохраняем локальную подписку (уведомление может прийти сразу).
        2. Создаём вебхук в InSales.
        3. Сохраняем insales_id.

        При неопределённом результате создания (таймаут, 5xx после записи)
        сверяем список подписок перед повтором.
        """
        # Шаг 1: локальная подписка
        sub = self._subs.create(
            store_id=store_id,
            generation=generation,
            topic=topic,
            base_url=self._base_url,
        )

        # Шаг 2: удалённое создание
        remote = self._create_remote_with_retry(client, topic, sub['address'])
        if remote is None:
            # Не удалось создать — возможно уже существует
            return self._reconcile(client, sub, topic)

        # Шаг 3: сохраняем insales_id
        remote_id = remote.get('id')
        if remote_id is not None:
            self._subs.update_insales_id(sub['id'], remote_id)

        return sub

    def _create_remote_with_retry(self, client: InSalesClient,
                                   topic: str, address: str) -> dict | None:
        """Создать вебхук в InSales с ограниченным числом повторов.

        Returns:
            Созданный вебхук или None, если не удалось (постоянная ошибка).
        """
        for attempt in range(self._retry.max_retries + 1):
            try:
                return client.webhooks.create(
                    topic=topic,
                    address=address,
                    format_type='json',
                )
            except ApiError as exc:
                if not exc.is_temporary:
                    return None
                delay = self._retry.get_delay(attempt, exc.retry_after)
                import time
                time.sleep(delay)
                continue
            except (OSError, TransportError):
                return None
        return None

    def _reconcile(self, client: InSalesClient,
                    sub: SubscriptionRow,
                    topic: str) -> SubscriptionRow | None:
        """Сверить локальную подписку с удалённым списком.

        Вызывается, когда создание подписки через API дало неопределённый
        результат. Ищет вебхук по теме И точному адресу callback,
        чтобы не присвоить чужой вебхук того же магазина/темы.
        """
        expected_address = sub.get('address', '')
        try:
            hooks = client.webhooks.list(per_page=250)
            for hook in hooks:
                if hook.get('topic') != topic:
                    continue
                # Проверяем точное совпадение адреса callback
                hook_address = hook.get('address', '')
                if hook_address and expected_address:
                    if hook_address == expected_address:
                        hook_id = hook.get('id')
                        if hook_id is not None:
                            self._subs.update_insales_id(sub['id'], hook_id)
                        return sub
                    # Адрес не совпадает — это чужой вебхук, пропускаем
                    continue
                # Если адрес не сохранён (старая подписка) — fallback на topic
                hook_id = hook.get('id')
                if hook_id is not None:
                    self._subs.update_insales_id(sub['id'], hook_id)
                return sub
        except (ApiError, OSError):
            pass

        # Вебхук не найден — оставляем локальную подписку для повтора
        return sub

    def unregister_all(self, client: InSalesClient,
                        store_id: str) -> int:
        """Отписаться от всех вебхуков магазина."""
        subs = self._subs.list_by_store(store_id)
        deleted = 0
        for sub in subs:
            insales_id = sub.get('insales_id')
            if insales_id is not None:
                try:
                    client.webhooks.delete(insales_id)
                    deleted += 1
                except (ApiError, OSError):
                    pass
        self._subs.delete_by_store(store_id)
        return deleted
