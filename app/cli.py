"""Интерфейс командной строки для управления приложением.

Использование:
    python -m app.cli serve          # Запустить сервер
    python -m app.cli db upgrade     # Применить миграции
    python -m app.cli db status      # Статус миграций
    python -m app.cli doctor         # Диагностика системы
    python -m app.cli webhook list   # Список последних вебхуков
    python -m app.cli webhook get <id>   # Детали вебхука
    python -m app.cli webhook retry <id> # Повтор сбойного вебхука
"""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from app.config import Settings
from app.db.connection import status as db_status
from app.db.connection import upgrade as db_upgrade

if TYPE_CHECKING:
    from app.repositories.webhook_inbox import WebhookInbox


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
        stores = store.list_stores()
        if not stores:
            print('  – Нет установленных магазинов')
        else:
            for s in stores:
                shop = s.get('shop', '?')
                store_id = s.get('id', '?')
                try:
                    # Получаем полные credentials магазина (с паролем)
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


def cmd_webhook(settings: Settings, sub: str, args: list[str]) -> int:
    """Управление webhook-событиями.

    Подкоманды:
        list [N]     — показать последние N событий (по умолч. 20)
        get <id>     — детали события
        retry <id>   — сбросить сбойное событие в pending для повтора
    """
    if not sub:
        print('Usage: python -m app.cli webhook (list|get|retry)')
        return 1

    from app.repositories.webhook_inbox import WebhookInbox
    from insales.store import Store

    store = Store(settings.db_path)
    inbox = WebhookInbox(store)

    if sub == 'list':
        limit = int(args[0]) if args else 20
        return _cmd_webhook_list(inbox, limit)

    if sub == 'get':
        if not args:
            print('Usage: python -m app.cli webhook get <id>')
            return 1
        return _cmd_webhook_get(inbox, args[0])

    if sub == 'retry':
        if not args:
            print('Usage: python -m app.cli webhook retry <id>')
            return 1
        return _cmd_webhook_retry(inbox, args[0])

    print(f'Unknown webhook subcommand: {sub}')
    return 1


def _cmd_webhook_list(inbox: WebhookInbox, limit: int) -> int:
    """Показать последние N событий."""

    # Прямой SQL, т.к. inbox.list_pending() показывает только pending
    with inbox._store._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            '''SELECT id, store_id, topic, state, attempts,
                      error, created_at
               FROM webhook_inbox
               ORDER BY id DESC
               LIMIT ?''',
            (limit,),
        ).fetchall()

    if not rows:
        print('Событий не найдено')
        return 0

    print(f'Последние {len(rows)} событий:')
    print()
    header = f'{"ID":<6} {"Store":<8} {"Topic":<25} {"State":<12} {"Attempts":<9} Error'
    print(header)
    print('-' * len(header))
    for r in rows:
        error = (r['error'] or '')[:40]
        print(f'{r["id"]:<6} {r["store_id"]:<8} {r["topic"]:<25} '
              f'{r["state"]:<12} {r["attempts"]:<9} {error}')
    return 0


def _cmd_webhook_get(inbox: WebhookInbox, event_id: str) -> int:
    """Показать детали события."""
    try:
        event = inbox.get(int(event_id))
    except (ValueError, TypeError):
        print(f'Ошибка: ID должен быть числом, получено: {event_id}')
        return 1

    if event is None:
        print(f'Событие #{event_id} не найдено')
        return 1

    print(f'Событие #{event["id"]}')
    print(f'  Store ID:     {event["store_id"]}')
    print(f'  Generation:   {event["generation"]}')
    print(f'  Topic:        {event["topic"]}')
    print(f'  State:        {event["state"]}')
    print(f'  Attempts:     {event["attempts"]} / {event["max_attempts"]}')
    print(f'  Created:      {event["created_at"]}')
    print(f'  Next attempt: {event["next_attempt_at"]}')
    print(f'  Error:        {event["error"] or "—"}')
    print(f'  Payload:      {(event.get("payload") or "—")[:500]}')
    return 0


def _cmd_webhook_retry(inbox: WebhookInbox, event_id: str) -> int:
    """Сбросить сбойное событие в pending для повторной обработки."""
    try:
        event_id_int = int(event_id)
    except (ValueError, TypeError):
        print(f'Ошибка: ID должен быть числом, получено: {event_id}')
        return 1

    event = inbox.get(event_id_int)
    if event is None:
        print(f'Событие #{event_id} не найдено')
        return 1

    if event['state'] != 'failed':
        print(f'Событие #{event_id} в состоянии "{event["state"]}", '
              f'ожидалось "failed"')
        return 1

    # Сбрасываем: state=pending, attempts=0, error=NULL, next_attempt_at=now
    import time

    with inbox._store._connect() as conn:  # noqa: SLF001
        conn.execute(
            '''UPDATE webhook_inbox
               SET state='pending', attempts=0, error=NULL,
                   next_attempt_at=?, locked_at=NULL, locked_by=NULL,
                   updated_at=julianday("now")
               WHERE id=? AND state='failed' ''',
            (time.time(), event_id_int),
        )

    print(f'Событие #{event_id} сброшено в pending и будет повторно обработано')
    return 0


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

    if cmd == 'webhook':
        sub = argv[1] if len(argv) > 1 else ''
        return cmd_webhook(settings, sub, argv[2:])

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
