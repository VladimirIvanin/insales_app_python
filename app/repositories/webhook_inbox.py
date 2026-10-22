"""Репозиторий inbox-событий вебхуков InSales.

Таблица webhook_inbox хранит сырые события и их статус обработки.
"""
from __future__ import annotations

import json
import time
from typing import Any

from insales.store import Store

InboxRow = dict[str, Any]

_MAX_ATTEMPTS = 5


class WebhookInbox:
    """Репозиторий входящих webhook-событий."""

    def __init__(self, store: Store):
        self._store = store

    def push(self, store_id: str, generation: int, topic: str,
             payload: Any, headers: dict[str, str] | None = None) -> int:
        """Сохранить входящее событие в inbox.

        Returns:
            ID созданной записи.
        """
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''INSERT INTO webhook_inbox
                   (store_id, generation, topic, payload, headers,
                    state, max_attempts, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?,
                           julianday('now'), julianday('now'))''',
                (store_id, generation, topic,
                 json.dumps(payload, ensure_ascii=False),
                 json.dumps(headers or {}),
                 _MAX_ATTEMPTS),
            )
            return cur.lastrowid

    def get(self, inbox_id: int) -> InboxRow | None:
        """Получить событие по ID."""
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                'SELECT * FROM webhook_inbox WHERE id=?', (inbox_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending(self, limit: int = 10) -> list[InboxRow]:
        """Выбрать события, готовые к обработке (не заблокированные)."""
        now = time.time()
        with self._store._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                '''SELECT * FROM webhook_inbox
                   WHERE state='pending'
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                     AND (locked_at IS NULL OR locked_at < ?)
                   ORDER BY created_at ASC
                   LIMIT ?''',
                (now, now - 300, limit),  # lock timeout 5 min
            ).fetchall()
        return [dict(r) for r in rows]

    def acquire(self, inbox_id: int, worker_id: str,
                lock_ttl: int = 300) -> bool:
        """Атомарно захватить событие для обработки.

        Args:
            inbox_id: ID события.
            worker_id: Идентификатор worker'а.
            lock_ttl: Время владения в секундах.

        Returns:
            True, если захват успешен.
        """
        now = time.time()
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''UPDATE webhook_inbox
                   SET locked_at=?, locked_by=?, updated_at=julianday("now")
                   WHERE id=? AND state='pending'
                     AND (locked_at IS NULL OR locked_at < ?)''',
                (now, worker_id, inbox_id, now - lock_ttl),
            )
            return cur.rowcount > 0

    def release(self, inbox_id: int) -> bool:
        """Освободить событие (снять блокировку)."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''UPDATE webhook_inbox
                   SET locked_at=NULL, locked_by=NULL,
                       updated_at=julianday("now")
                   WHERE id=?''',
                (inbox_id,),
            )
            return cur.rowcount > 0

    def mark_success(self, inbox_id: int) -> bool:
        """Пометить событие как успешно обработанное."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''UPDATE webhook_inbox
                   SET state='processed', locked_at=NULL, locked_by=NULL,
                       next_attempt_at=NULL,
                       updated_at=julianday("now")
                   WHERE id=?''',
                (inbox_id,),
            )
            return cur.rowcount > 0

    def mark_failed(self, inbox_id: int,
                    error: str, permanent: bool = False) -> bool:
        """Пометить событие как ошибочное.

        Если permanent=True — событие переводится в 'failed' без повтора.
        Иначе — увеличивается счётчик попыток и планируется повтор.
        """
        row = self.get(inbox_id)
        if row is None:
            return False

        attempts = row['attempts'] + 1
        if permanent or attempts >= row['max_attempts']:
            new_state = 'failed'
            next_attempt = None
        else:
            new_state = 'pending'
            delay = min(60 * (2 ** (attempts - 1)), 3600)  # 1min, 2min, 4min...
            next_attempt = time.time() + delay

        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''UPDATE webhook_inbox
                   SET state=?, attempts=?, error=?,
                       next_attempt_at=?, locked_at=NULL, locked_by=NULL,
                       updated_at=julianday("now")
                   WHERE id=?''',
                (new_state, attempts, error, next_attempt, inbox_id),
            )
            return cur.rowcount > 0

    def cancel_by_store(self, store_id: str) -> int:
        """Отменить все pending-события магазина (после uninstall/reinstall)."""
        with self._store._connect() as conn:  # noqa: SLF001
            cur = conn.execute(
                '''UPDATE webhook_inbox
                   SET state='cancelled', updated_at=julianday("now")
                   WHERE store_id=? AND state='pending' ''',
                (store_id,),
            )
            return cur.rowcount

    def count_pending(self, store_id: str | None = None) -> int:
        """Количество необработанных событий."""
        with self._store._connect() as conn:  # noqa: SLF001
            if store_id:
                row = conn.execute(
                    'SELECT COUNT(*) FROM webhook_inbox '
                    'WHERE state="pending" AND store_id=?',
                    (store_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    'SELECT COUNT(*) FROM webhook_inbox '
                    'WHERE state="pending"',
                ).fetchone()
            return row[0]
