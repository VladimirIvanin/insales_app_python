"""Фабрика Flask-приложения."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from flask import Flask

from app.config import Settings
from app.middleware import SessionMiddleware
from app.routes import create_routes
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

    # --- Инициализация БД ---
    store = Store(settings.db_path)

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

    # --- Обработчики ошибок ---
    @app.errorhandler(404)
    def not_found(e):
        return 'Not found', 404

    @app.errorhandler(500)
    def server_error(e):
        return 'Internal server error', 500

    return app
