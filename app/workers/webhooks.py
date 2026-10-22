"""Фоновый worker для обработки webhook-событий.

Атомарно забирает задание из inbox с ограниченным сроком владения.
Dispatcher определяет handler по теме.
Временные ошибки повторяются с задержкой.
Окончательные ошибки сохраняются для диагностики.
"""
from __future__ import annotations

import logging
import os
import time
from threading import Event, Thread

from app.integrations.insales.webhook_dispatcher import WebhookDispatcher
from app.repositories.webhook_inbox import WebhookInbox

log = logging.getLogger(__name__)


class WebhookWorker:
    """Фоновый worker для webhook-событий.

    Запускается в отдельном потоке. Периодически проверяет inbox
    на наличие новых заданий.
    """

    def __init__(self, inbox: WebhookInbox,
                 dispatcher: WebhookDispatcher,
                 poll_interval: float = 1.0,
                 batch_size: int = 10,
                 worker_id: str | None = None):
        self._inbox = inbox
        self._dispatcher = dispatcher
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._worker_id = worker_id or f'wh-{os.getpid()}-{time.time():.0f}'
        self._thread: Thread | None = None
        self._stop_event = Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Запустить worker в фоновом потоке."""
        if self.running:
            log.warning('Webhook worker already running')
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, daemon=True,
                              name='webhook-worker')
        self._thread.start()
        log.info('Webhook worker started: %s', self._worker_id)

    def stop(self, timeout: float = 5.0) -> None:
        """Остановить worker."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            log.info('Webhook worker stopped: %s', self._worker_id)

    def _run_loop(self) -> None:
        """Основной цикл worker'а."""
        while not self._stop_event.is_set():
            try:
                self._process_batch()
            except Exception as exc:
                log.error('Webhook worker error: %s', exc, exc_info=True)
            self._stop_event.wait(self._poll_interval)

    def _process_batch(self) -> None:
        """Захватить и обработать пачку заданий."""
        events = self._inbox.list_pending(limit=self._batch_size)
        for event in events:
            if self._stop_event.is_set():
                break
            self._process_one(event)

    def _process_one(self, event: dict) -> None:
        """Обработать одно событие.

        Последовательность:
        1. Атомарно захватить событие (acquire).
        2. Если захват не удался — пропустить (другой worker уже взял).
        3. Вызвать dispatcher.
        4. При успехе — mark_success.
        5. При ошибке — mark_failed (с повтором или без).
        """
        event_id = event['id']

        # Шаг 1: захват
        if not self._inbox.acquire(event_id, self._worker_id):
            return

        store_id = event['store_id']
        generation = event['generation']
        topic = event['topic']
        payload = event.get('payload', '{}')

        import json
        try:
            payload_data = json.loads(payload) if isinstance(payload, str) else payload
        except (json.JSONDecodeError, TypeError):
            payload_data = payload

        # Шаг 2: обработка
        try:
            success = self._dispatcher.dispatch(
                store_id=store_id,
                generation=generation,
                topic=topic,
                payload=payload_data,
            )

            if success:
                self._inbox.mark_success(event_id)
                log.debug('Event %s processed: topic=%s store=%s',
                           event_id, topic, store_id)
            else:
                # No handler or handler returned False — не повторяем
                self._inbox.mark_failed(
                    event_id,
                    error=f'No handler for topic: {topic}',
                    permanent=True,
                )
        except Exception as exc:
            log.error('Event %s failed: %s', event_id, exc, exc_info=True)
            self._inbox.mark_failed(
                event_id,
                error=str(exc)[:500],
                permanent=False,
            )
