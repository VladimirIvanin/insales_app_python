"""Flask routes для приёма webhook-уведомлений InSales.

Endpoints:
- POST /webhook/insales?token=<secret> — приём уведомления
- GET  /webhook/insales                — проверка доступности (health)

Cookie-сессия для этого маршрута не нужна. Магазин определяется
через найденную подписку по секрету в URL.

Секретный URL скрывается в логах приложения.
"""
from __future__ import annotations

import logging

from flask import Blueprint, request

from app.integrations.insales.webhook_receiver import WebhookReceiver

log = logging.getLogger(__name__)

# Максимальный размер тела запроса
MAX_CONTENT_LENGTH = 1_100_000


def create_webhook_routes(receiver: WebhookReceiver,
                           max_content_length: int | None = None) -> Blueprint:
    """Создать blueprint с webhook endpoint'ами.

    Args:
        receiver: WebhookReceiver для обработки уведомлений.
        max_content_length: Максимальный размер тела запроса в байтах.
                            По умолчанию 1_100_000.

    Returns:
        Blueprint для регистрации в Flask-приложении.
    """
    if max_content_length is None:
        max_content_length = MAX_CONTENT_LENGTH

    blueprint = Blueprint('webhooks', __name__, static_folder=None)

    @blueprint.route('/webhook/insales', methods=['POST'])
    def webhook_receive():
        token = request.args.get('token', '')
        if not token:
            return 'Missing token', 400

        body = request.get_data()
        headers = dict(request.headers)

        result = receiver.receive(
            token=token,
            body=body,
            headers=headers,
        )

        if result.accepted:
            log.info(
                'Webhook accepted: topic=%s store=%s inbox_id=%s',
                result.topic, result.store_id, result.inbox_id,
            )
            return 'OK', 200

        if result.status == 404:
            log.warning('Webhook rejected: unknown token %s...', token[:8])
            return result.error or 'Not found', 404

        log.warning(
            'Webhook rejected: status=%s error=%s',
            result.status, result.error,
        )
        return result.error or 'Bad request', result.status

    @blueprint.route('/webhook/insales', methods=['GET'])
    def webhook_health():
        return 'Webhook endpoint ready', 200

    return blueprint
