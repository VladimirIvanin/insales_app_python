#!/usr/bin/env python3
"""Локальный launcher устанавливаемого приложения InSales.

Запуск:
    cp .env.example .env   # и заполнить
    python run.py

Для production используйте WSGI-сервер (gunicorn, waitress):
    from app import create_app
    application = create_app()
"""
from __future__ import annotations

import os

from app.config import Settings
from app.factory import create_app


def main() -> None:
    os.umask(0o077)
    settings = Settings.from_env()
    app = create_app(settings)
    app.run(host=settings.bind_host, port=settings.port,
            debug=settings.flask_debug)


if __name__ == '__main__':
    main()
