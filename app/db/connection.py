"""Управление подключением к SQLite и миграциями схемы."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# Миграции: (номер, описание, файл.sql или inline SQL)
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, 'Начальная схема', '0001_initial.sql'),
    (2, 'Добавлен generation в sessions', '0002_sessions_generation.sql'),
    (3, 'Webhook-подписки и inbox', '0003_webhooks.sql'),
    (4, 'Tombstones для отменённых установок', '0004_store_tombstones.sql'),
    (5, 'Удаление webhook-таблиц', '0005_remove_webhooks.sql'),
    (6, 'Счётчик поколений', '0006_generation_counter.sql'),
    (7, 'Добавлен generation в challenges', '0007_challenges_generation.sql'),
]

_SCHEMA_DIR = Path(__file__).parent / 'migrations'


class MigrationError(Exception):
    """Ошибка применения миграции."""


def _current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            'SELECT COALESCE(MAX(version), 0) FROM _schema_version'
        ).fetchone()
        return row[0]
    except sqlite3.OperationalError:
        return 0


def _migration_0002_sql(conn: sqlite3.Connection) -> str | None:
    """Проверить, нужно ли добавить generation в sessions.

    Вызывается после применения миграции 1 (таблица sessions уже создана
    в текущей транзакции conn).
    """
    try:
        cols = conn.execute('PRAGMA table_info(sessions)').fetchall()
        if any(col[1] == 'generation' for col in cols):
            return None  # колонка уже есть
    except sqlite3.OperationalError:
        pass  # таблицы нет — будет создана миграцией 1
    return 'ALTER TABLE sessions ADD COLUMN generation INTEGER NOT NULL DEFAULT 0;'


def _migration_0007_sql(conn: sqlite3.Connection) -> str | None:
    """Проверить, нужно ли добавить generation в challenges.

    Вызывается после миграции 6.
    """
    try:
        cols = conn.execute('PRAGMA table_info(challenges)').fetchall()
        if any(col[1] == 'generation' for col in cols):
            return None  # колонка уже есть
    except sqlite3.OperationalError:
        pass
    return 'ALTER TABLE challenges ADD COLUMN generation INTEGER NOT NULL DEFAULT 0;'


def _apply_migration(conn: sqlite3.Connection, version: int,
                     sql: str | None) -> None:
    """Применить миграцию.

    Если sql=None — SQL не выполняется (колонка уже существует),
    записываем только версию схемы.
    """
    try:
        if sql is not None:
            for statement in sql.split(';'):
                stmt = statement.strip()
                if stmt:
                    conn.execute(stmt)
        conn.execute(
            'INSERT OR IGNORE INTO _schema_version (version, applied_at) VALUES (?, ?)',
            (version, time.time()),
        )
    except Exception as exc:
        raise MigrationError(f'Migration {version} failed') from exc


def upgrade(db_path: Path) -> None:
    """Применить все неприменённые миграции к БД.

    Создаёт каталог для БД при необходимости.
    Может вызываться многократно — идемпотентна.
    """
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')

        # Создаём таблицу версий схемы, если её нет
        conn.execute('''
            CREATE TABLE IF NOT EXISTS _schema_version (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        ''')

        # Захватываем write-блокировку до всех проверок схемы
        conn.execute('BEGIN IMMEDIATE')

        current = _current_version(conn)
        latest = max(v for v, _, _ in MIGRATIONS) if MIGRATIONS else 0

        if current > latest:
            conn.rollback()
            raise MigrationError(
                f'Database schema version {current} is newer than '
                f'application supports ({latest}). Downgrade is not supported.'
            )

        for version, _desc, source in MIGRATIONS:
            if version <= current:
                continue

            # Подготовка SQL
            if version == 2:
                sql = _migration_0002_sql(conn)
            elif version == 7:
                sql = _migration_0007_sql(conn)
            elif source.endswith('.sql'):
                sql_path = _SCHEMA_DIR / source
                if not sql_path.exists():
                    conn.rollback()
                    raise MigrationError(
                        f'Migration file not found: {sql_path}'
                    )
                sql = sql_path.read_text(encoding='utf-8')
            else:
                sql = source

            _apply_migration(conn, version, sql)

        conn.commit()
    finally:
        conn.close()


def status(db_path: Path) -> list[dict]:
    """Показать статус миграций без изменения схемы.

    Не создаёт _schema_version таблицу — если её нет, все миграции
    считаются неприменёнными (current = 0).
    """
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        result = []
        for version, desc, _ in MIGRATIONS:
            result.append({
                'version': version,
                'description': desc,
                'applied': False,
            })
        result.append({
            'version': 'current',
            'description': 'Текущая версия схемы',
            'applied': 0,
        })
        return result

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.OperationalError:
        result = []
        for version, desc, _ in MIGRATIONS:
            result.append({
                'version': version,
                'description': desc,
                'applied': False,
            })
        result.append({
            'version': 'current',
            'description': 'Текущая версия схемы',
            'applied': 0,
        })
        return result

    try:
        conn.execute('PRAGMA journal_mode=WAL')
        current = _current_version(conn)
    finally:
        conn.close()

    result = []
    for version, desc, _ in MIGRATIONS:
        result.append({
            'version': version,
            'description': desc,
            'applied': version <= current,
        })
    result.append({
        'version': 'current',
        'description': 'Текущая версия схемы',
        'applied': current,
    })
    return result


@contextmanager
def connect_raw(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Открыть подключение к SQLite без вызова миграций.

    Не выполняет DDL — вызывающий должен накатить upgrade до
    открытия соединения (через app.db.connection.upgrade или
    из create_app).

    Устанавливает WAL и foreign_keys.
    """
    db_path = Path(db_path).resolve()

    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
