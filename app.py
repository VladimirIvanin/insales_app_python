#!/usr/bin/env python3
"""Точка входа устанавливаемого приложения InSales.

Запуск:
    export FLASK_APP=app.py
    export FLASK_ENV=development
    flask run

Или напрямую: python3 app.py
"""
import os

from flask import Flask

from app.middleware import SessionMiddleware
from app.routes import create_routes
from insales.lifecycle import Lifecycle
from insales.store import Store


def create_app() -> Flask:
    """Создать и настроить Flask-приложение."""
    app = Flask(__name__)

    # --- Конфигурация из окружения ---
    app_id = os.environ['INSALES_APP_ID']
    app_secret = os.environ['INSALES_APP_SECRET']
    base_url = os.environ['APP_BASE_URL'].rstrip('/')
    dev = os.environ.get('LOCAL_DEMO') == '1'
    db_path = os.environ.get(
        'APP_DB',
        os.path.join(
            os.environ.get('APP_RUNTIME_DIR', 'runtime'),
            'app.sqlite3',
        ),
    )

    # --- Секретный ключ Flask для flash/session ---
    app.secret_key = os.environ.get(
        'FLASK_SECRET_KEY',
        os.urandom(32).hex(),
    )

    # --- Инициализация слоёв ---
    store = Store(db_path)
    lifecycle = Lifecycle(store, app_id, app_secret, dev=dev)
    mw = SessionMiddleware(store, app_id, app_secret, dev=dev)

    # --- Регистрация routes ---
    blueprint = create_routes(store, lifecycle, mw, base_url)
    app.register_blueprint(blueprint)

    # --- Обработчик ошибок ---
    @app.errorhandler(404)
    def not_found(e):
        return 'Not found', 404

    @app.errorhandler(500)
    def server_error(e):
        return 'Internal server error', 500

    return app


if __name__ == '__main__':
    os.umask(0o077)
    app = create_app()
    host = os.environ.get('BIND_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8765'))
    app.run(host=host, port=port, debug=(os.environ.get('FLASK_ENV') == 'development'))
