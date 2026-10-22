"""Фабрика Flask-приложения."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from flask import Flask

from app.config import Settings
from app.integrations.insales.handlers.orders import (
    handle_order_create,
    handle_order_delete,
    handle_order_update,
)
from app.integrations.insales.subscriptions import SubscriptionService
from app.integrations.insales.webhook_dispatcher import WebhookDispatcher
from app.integrations.insales.webhook_receiver import WebhookReceiver
from app.middleware import SessionMiddleware
from app.repositories.webhook_inbox import WebhookInbox
from app.repositories.webhook_subscriptions import WebhookSubscriptions
from app.routes import create_routes
from app.webhook_routes import MAX_CONTENT_LENGTH, create_webhook_routes
from app.workers.webhooks import WebhookWorker
from insales.lifecycle import Lifecycle
from insales.store import Store

_SENSITIVE_PARAMS = re.compile(
    r'(token|password|secret|install_token|app_secret)[^&]*=[^&]+',
    re.IGNORECASE,
)


class MaskedRequestLogFilter(logging.Filter):
    """Маскировать query-параметры с credentials в логах Werkzeug."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, 'args') and record.args:
            args = list(record.args)
            for i, arg in enumerate(args):
                if isinstance(arg, str) and '?' in arg:
                    path, qs = arg.split('?', 1)
                    masked = _SENSITIVE_PARAMS.sub(r'\1=[REDACTED]', qs)
                    args[i] = f'{path}?{masked}'
            record.args = tuple(args)
        return True


def create_app(settings: Settings | None = None) -> Flask:
    """Создать и настроить Flask-приложение.

    Args:
        settings: Готовая конфигурация. Если None — загружает из env.
    """
    import app.config as config_module

    if settings is None:
        settings = config_module.Settings.from_env()

    # --- Маскировка логов ---
    log = logging.getLogger('werkzeug')
    log.addFilter(MaskedRequestLogFilter())

    # templates — рядом с factory.py
    templates_dir = str(Path(__file__).parent / 'templates')
    app = Flask(__name__, template_folder=templates_dir)

    app.secret_key = settings.flask_secret_key

    # --- Лимит тела webhook-запроса на уровне Flask ---
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

    # --- Инициализация БД ---
    store = Store(settings.db_path)

    # --- Webhook subscriptions repository (нужен до Lifecycle) ---
    webhook_subs = WebhookSubscriptions(store)

    # --- Lifecycle ---
    lifecycle = Lifecycle(
        store=store,
        app_id=settings.app_id,
        app_secret=settings.app_secret,
        dev=settings.dev,
        allowed_shops=set(settings.allowed_shops),
    )

    # --- Middleware ---
    mw = SessionMiddleware(store, settings.app_id, settings.app_secret,
                           dev=settings.dev)

    # --- Routes ---
    blueprint = create_routes(store, lifecycle, mw, settings.base_url)
    app.register_blueprint(blueprint)

    # --- Webhook infrastructure ---
    webhook_inbox = WebhookInbox(store)
    subscription_service = SubscriptionService(webhook_subs, settings.base_url)

    # Подключаем subscription_service к lifecycle для автоматической подписки
    lifecycle._subscription_service = subscription_service  # noqa: SLF001

    webhook_dispatcher = WebhookDispatcher()
    webhook_dispatcher.register('orders/create', handle_order_create)
    webhook_dispatcher.register('orders/update', handle_order_update)
    webhook_dispatcher.register('orders/delete', handle_order_delete)

    webhook_receiver = WebhookReceiver(webhook_subs, webhook_inbox)

    wh_blueprint = create_webhook_routes(webhook_receiver,
                                         max_content_length=MAX_CONTENT_LENGTH)
    app.register_blueprint(wh_blueprint)

    # --- Webhook worker ---
    webhook_worker = WebhookWorker(webhook_inbox, webhook_dispatcher,
                                    store=store)

    # Сохраняем ссылки для доступа из middleware/routes
    app.extensions = {
        'store': store,
        'lifecycle': lifecycle,
        'mw': mw,
        'webhook_subs': webhook_subs,
        'webhook_inbox': webhook_inbox,
        'subscription_service': subscription_service,
        'webhook_dispatcher': webhook_dispatcher,
        'webhook_worker': webhook_worker,
    }

    # --- Запуск ---
    lifecycle.start()
    webhook_worker.start()

    # --- Обработчики ошибок ---
    @app.errorhandler(404)
    def not_found(e):
        return 'Not found', 404

    @app.errorhandler(500)
    def server_error(e):
        return 'Internal server error', 500

    return app
