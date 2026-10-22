"""Интерфейс командной строки для управления приложением.

Использование:
    python -m app.cli serve          # Запустить сервер
    python -m app.cli db upgrade     # Применить миграции
    python -m app.cli db status      # Статус миграций
"""
from __future__ import annotations

import os
import sys

from app.config import Settings
from app.db.connection import status as db_status
from app.db.connection import upgrade as db_upgrade


def cmd_serve(settings: Settings) -> None:
    """Запустить HTTP-сервер."""
    os.umask(0o077)

    # Upgrade БД перед запуском
    db_upgrade(settings.db_path)

    from app.factory import create_app
    app = create_app(settings)
    app.run(host=settings.bind_host, port=settings.port,
            debug=settings.flask_debug)


def cmd_db_upgrade(settings: Settings) -> None:
    """Применить миграции БД."""
    db_upgrade(settings.db_path)
    print(f'Database upgraded: {settings.db_path}')


def cmd_db_status(settings: Settings) -> None:
    """Показать статус миграций."""
    for entry in db_status(settings.db_path):
        v = entry['version']
        desc = entry['description']
        applied = entry['applied']
        if v == 'current':
            print(f'  Current schema version: {applied}')
        else:
            mark = '✓' if applied else '✗'
            print(f'  [{mark}] v{v}: {desc}')


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print(__doc__)
        return 1

    try:
        settings = Settings.from_env()
    except Exception as e:
        print(f'Configuration error: {e}', file=sys.stderr)
        return 1

    cmd = argv[0]

    if cmd == 'serve':
        cmd_serve(settings)
        return 0

    if cmd == 'db':
        sub = argv[1] if len(argv) > 1 else ''
        if sub == 'upgrade':
            cmd_db_upgrade(settings)
        elif sub == 'status':
            cmd_db_status(settings)
        else:
            print('Usage: python -m app.cli db (upgrade|status)')
            return 1
        return 0

    print(f'Unknown command: {cmd}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
