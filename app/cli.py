"""Интерфейс командной строки для управления приложением.

Использование:
    insales-app serve          # Запустить сервер
    insales-app db upgrade     # Применить миграции
    insales-app db status      # Статус миграций
    insales-app doctor         # Диагностика системы
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


def cmd_doctor(settings: Settings) -> int:
    """Диагностика системы: конфигурация, БД, API магазинов."""
    print('🔍 Doctor: диагностика системы')
    print()

    issues = 0

    # 1. Конфигурация
    print('── Конфигурация ──')
    checks = [
        ('INSALES_APP_ID', settings.app_id),
        ('INSALES_APP_SECRET', '***' if settings.app_secret else ''),
        ('APP_BASE_URL', settings.base_url),
    ]
    for name, value in checks:
        if value:
            masked = '***' if 'SECRET' in name else str(value)
            print(f'  ✓ {name} = {masked}')
        else:
            print(f'  ✗ {name} не задан')
            issues += 1

    print()

    # 2. База данных
    print('── База данных ──')
    db_path = settings.db_path
    if db_path.exists():
        size = db_path.stat().st_size
        print(f'  ✓ Файл БД: {db_path} ({_fmt_size(size)})')
    else:
        print(f'  ✗ Файл БД не найден: {db_path}')
        issues += 1

    try:
        migrations = db_status(settings.db_path)
        applied_count = sum(1 for m in migrations if m.get('applied') is True)
        total = sum(1 for m in migrations if isinstance(m['version'], int))
        if applied_count == total:
            print(f'  ✓ Миграции: {applied_count}/{total} применены')
        else:
            print(f'  ✗ Миграции: {applied_count}/{total} применены (ожидается {total})')
            issues += 1
        for m in migrations:
            if 'version' in m and m['version'] != 'current':
                mark = '✓' if m['applied'] else '✗'
                print(f'    [{mark}] v{m["version"]}: {m["description"]}')
    except Exception as exc:
        print(f'  ✗ Ошибка проверки миграций: {exc}')
        issues += 1

    print()

    # 3. Подключение к API магазинов
    print('── API магазинов ──')
    try:
        from insales.client import InSalesClient
        from insales.store import Store

        store = Store(settings.db_path)
        if not settings.db_path.exists():
            print('  – База данных отсутствует — проверка API недоступна')
        else:
            try:
                stores = store.list_stores()
            except Exception as exc:
                print(f'  – Невозможно прочитать список магазинов: {exc}')
                stores = []
            if not stores:
                print('  – Нет установленных магазинов')
            else:
                for s in stores:
                    shop = s.get('shop', '?')
                    store_id = s.get('id', '?')
                    try:
                        full = store.get_store(store_id)
                        password = full['password'] if full else ''
                        if not password:
                            print(f'  ✗ Магазин #{store_id}: {shop} — нет credentials')
                            issues += 1
                            continue
                        client = InSalesClient(
                            shop=shop,
                            app_id=settings.app_id,
                            password=password,
                            dev=settings.dev,
                        )
                        account = client.get_account()[0]
                        title = account.get('title', shop)
                        print(f'  ✓ Магазин #{store_id}: {title} ({shop})')
                    except Exception as exc:
                        print(f'  ✗ Магазин #{store_id}: {shop} — {exc}')
                        issues += 1
    except Exception as exc:
        print(f'  ✗ Ошибка проверки магазинов: {exc}')
        issues += 1

    print()
    if issues:
        print(f'❌ Найдено проблем: {issues}')
    else:
        print('✅ Система работает корректно')
    return 1 if issues else 0


def _fmt_size(size: int) -> str:
    for unit in ('B', 'KB', 'MB'):
        if size < 1024:
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


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

    if cmd == 'doctor':
        return cmd_doctor(settings)

    if cmd == 'db':
        sub = argv[1] if len(argv) > 1 else ''
        if sub == 'upgrade':
            cmd_db_upgrade(settings)
        elif sub == 'status':
            cmd_db_status(settings)
        else:
            print('Usage: insales-app db (upgrade|status)')
            return 1
        return 0

    print(f'Unknown command: {cmd}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
