"""Диспетчер webhook-событий по теме.

Находит handler для темы и вызывает его.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# Тип handler'а: (store_id, generation, topic, payload) -> bool
HandlerFunc = Callable[[str, int, str, Any], bool]


class WebhookDispatcher:
    """Диспетчер webhook-событий.

    Регистрирует handler'ы по темам и направляет событие в нужный.
    """

    def __init__(self):
        self._handlers: dict[str, HandlerFunc] = {}

    def register(self, topic: str, handler: HandlerFunc) -> None:
        """Зарегистрировать handler для темы.

        Args:
            topic: Тема события (например, 'orders/update').
            handler: Функция-обработчик.
        """
        if topic in self._handlers:
            log.warning('Handler for %s is being replaced', topic)
        self._handlers[topic] = handler

    def dispatch(self, store_id: str, generation: int,
                 topic: str, payload: Any) -> bool:
        """Направить событие в handler по теме.

        Args:
            store_id: ID магазина.
            generation: Поколение установки.
            topic: Тема события.
            payload: Тело события (распарсенный JSON).

        Returns:
            True, если обработка успешна.
            False, если handler не найден или вернул False.

        Raises:
            Exception: Исключение от handler'а (worker обрабатывает как временную ошибку).
        """
        handler = self._handlers.get(topic)
        if handler is None:
            log.warning('No handler for topic: %s (store=%s)',
                         topic, store_id)
            return False

        return handler(store_id, generation, topic, payload)

    def has_handler(self, topic: str) -> bool:
        """Проверить, зарегистрирован ли handler для темы."""
        return topic in self._handlers
