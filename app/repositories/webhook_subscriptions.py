"""Репозиторий webhook-подписок магазина.

Таблица webhook_subscriptions привязана к магазину и его поколению.
"""
from __future__ import annotations

import secrets
from typing import Any

from insales.store import Store

SubscriptionRow = dict[str, Any]

_STATES = frozenset({'active', 'inactive', 'deleted'})


class WebhookSubscriptions:
    """Репозиторий подписок на webhook-уведомления InSales."""

    def __init__(self, store: Store):
        self._store = store

    def _generate_secret(self) -> str:
        return secrets.token_urlsafe(32)

    def _build_address(self, base_url: str, secret: str) -> str:
        base = base_url.rstrip('/')
        return f'{base}/webhook/insales?token={secret}'

    def create(self, store_id: str, generation: int, topic: str,
               base_url: str, format_type: str = 'json') -> SubscriptionRow:
        """Создать локальную подписку до регистрации в InSales.

        Returns:
            Созданная запись подписки.
        """
        secret = self._generate_secret()
        address = self._build_address(base_url, secret)

        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                '''INSERT INTO webhook_subscriptions
                   (store_id, generation, topic, callback_secret,
                    address, format_type, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', julianday('now'), julianday('now'))
                   ON CONFLICT(store_id, topic) DO UPDATE SET
                       generation=excluded.generation,
                       callback_secret=excluded.callback_secret,
                       address=excluded.address,
                       format_type=excluded.format_type,
                       state='active',
                       insales_id=NULL,
                       updated_at=julianday('now')
                   WHERE generation <= excluded.generation''',
                (store_id, generation, topic, secret,
                 address, format_type),
            )

        # После upsert читаем запись по store_id+topic
        return self.get_by_store_and_topic(store_id, topic)

    def get(self, sub_id: int) -> SubscriptionRow | None:
        """Получить подписку по ID."""
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                'SELECT * FROM webhook_subscriptions WHERE id=?', (sub_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_secret(self, secret: str) -> SubscriptionRow | None:
        """Найти подписку по callback_secret."""
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                'SELECT * FROM webhook_subscriptions WHERE callback_secret=?',
                (secret,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_store_and_topic(self, store_id: str,
                                topic: str) -> SubscriptionRow | None:
        """Найти подписку магазина по теме."""
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                'SELECT * FROM webhook_subscriptions '
                'WHERE store_id=? AND topic=?',
                (store_id, topic),
            ).fetchone()
        return dict(row) if row else None

    def list_by_store(self, store_id: str) -> list[SubscriptionRow]:
        """Все активные подписки магазина."""
        with self._store._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                'SELECT * FROM webhook_subscriptions '
                'WHERE store_id=? AND state="active"',
                (store_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_insales_id(self, sub_id: int,
                           insales_id: int) -> bool:
        """Сохранить ID вебхука InSales после удалённого создания."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                'UPDATE webhook_subscriptions SET insales_id=?, '
                'updated_at=julianday("now") WHERE id=?',
                (insales_id, sub_id),
            )
            return cur.rowcount > 0

    def mark_inactive(self, sub_id: int) -> bool:
        """Пометить подписку как неактивную."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                'UPDATE webhook_subscriptions SET state="inactive", '
                'updated_at=julianday("now") WHERE id=?',
                (sub_id,),
            )
            return cur.rowcount > 0

    def delete_by_store(self, store_id: str) -> int:
        """Удалить (мягко) все подписки магазина."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                'UPDATE webhook_subscriptions SET state="deleted", '
                'updated_at=julianday("now") WHERE store_id=?',
                (store_id,),
            )
            return cur.rowcount

    def mark_inactive_by_generation(self, store_id: str,
                                     old_generation: int) -> int:
        """Пометить неактивными подписки старого поколения."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                'UPDATE webhook_subscriptions SET state="inactive", '
                'updated_at=julianday("now") '
                'WHERE store_id=? AND generation<?',
                (store_id, old_generation),
            )
            return cur.rowcount
