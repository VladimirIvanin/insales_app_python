"""Handler'ы webhook-событий InSales.

Каждый handler — функция, принимающая (store_id, generation, topic, payload)
и возвращающая bool.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def handle_order_update(store_id: str, generation: int,
                         topic: str, payload: Any) -> bool:
    """Обработать событие orders/update.

    На данном этапе — логирование и сохранение факта обработки.
    В дальнейшем здесь будет бизнес-логика.
    """
    order_id = None
    if isinstance(payload, dict):
        order_id = payload.get('id') or payload.get('order', {}).get('id')

    log.info(
        'Order update: store=%s order=%s topic=%s generation=%s',
        store_id, order_id, topic, generation,
    )
    return True


def handle_order_create(store_id: str, generation: int,
                         topic: str, payload: Any) -> bool:
    """Обработать событие orders/create."""
    order_id = None
    if isinstance(payload, dict):
        order_id = payload.get('id') or payload.get('order', {}).get('id')

    log.info(
        'Order created: store=%s order=%s topic=%s generation=%s',
        store_id, order_id, topic, generation,
    )
    return True


def handle_order_delete(store_id: str, generation: int,
                         topic: str, payload: Any) -> bool:
    """Обработать событие orders/delete."""
    order_id = None
    if isinstance(payload, dict):
        order_id = payload.get('id')

    log.info(
        'Order deleted: store=%s order=%s topic=%s generation=%s',
        store_id, order_id, topic, generation,
    )
    return True
