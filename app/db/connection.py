"""Управление подключением к SQLite и миграциями схемы."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION_TABLE = '''
    CREATE TABLE IF NOT EXISTS _schema_version (
        version INTEGER PRIMARY KEY,
        applied_at REAL NOT NULL
    );
'''

# Миграции: (номер, описание, файл.sql или inline SQL)
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, 'Начальная схема', '0001_initial.sql'),
    (2, 'Добавлен generation в sessions', '0002_sessions_generation.sql'),
    (3, 'Webhook-подписки и inbox', '0003_webhooks.sql'),
]

_SCHEMA_DIR = Path(__file__).parent / 'migrations'


class MigrationError(Exception):
    """Ошибка применения миграции."""


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        'SELECT COALESCE(MAX(version), 0) FROM _schema_version'
    ).fetchone()
    return row[0]


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


def _apply_migration(conn: sqlite3.Connection, version: int,
                     sql: str | None, already_in_transaction: bool = False) -> None:
    """Применить миграцию.

    Если sql=None — SQL не выполняется (колонка уже существует),
    записываем только версию схемы.
    Если already_in_transaction=True — вызывающий уже открыл BEGIN IMMEDIATE.
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
    except Exception:
        raise


def upgrade(db_path: Path) -> None:
    """Применить все неприменённые миграции к БД."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA_VERSION_TABLE)

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
            elif source.endswith('.sql'):
                sql_path = _SCHEMA_DIR / source
                if not sql_path.exists():
                    conn.rollback()
                    raise MigrationError(
                        f'Migration file not found: {sql_path}'
                    )
                sql = sql_path.read_text()
            else:
                sql = source

            _apply_migration(conn, version, sql, already_in_transaction=True)

        conn.commit()
    finally:
        conn.close()


def status(db_path: Path) -> list[dict]:
    """Показать статус миграций.

    Если база данных не существует (файл или каталог отсутствует),
    возвращает статус нулевой версии вместо ошибки соединения.
    """
    db_path = Path(db_path).resolve()
    if not db_path.parent.exists():
        # Родительский каталог не создан — база ещё не инициализирована
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
        # База недоступна (например, отсутствует каталог)
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
        conn.executescript(SCHEMA_VERSION_TABLE)
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
def connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Открыть подключение, гарантируя, что схема актуальна."""
    from app.db.connection import upgrade as run_upgrade

    db_path = Path(db_path).resolve()
    run_upgrade(db_path)

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
