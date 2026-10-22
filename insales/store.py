"""Хранилище данных приложения InSales (SQLite).

Таблицы:
- stores: установленные магазины (credentials, title)
- installs: кандидаты установки (ожидают фоновой проверки)
- challenges: одноразовые nonce для autologin
- sessions: пользовательские сессии

Credentials в SQLite не зашифрованы.
Для production требуется encryption at rest.
"""
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

StoreRow = dict
SessionRow = dict


class StoreError(Exception):
    """Ошибка хранилища (магазин не найден, дубликат и т.д.)."""


class Store:
    """Хранилище данных приложения на SQLite."""

    SCHEMA = '''
        CREATE TABLE IF NOT EXISTS stores (
            id           TEXT PRIMARY KEY,
            shop         TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL,
            title        TEXT DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS installs (
            candidate    TEXT PRIMARY KEY,
            store_id     TEXT NOT NULL,
            shop         TEXT NOT NULL,
            password     TEXT NOT NULL,
            created_at   REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS challenges (
            sid          TEXT PRIMARY KEY,
            store_id     TEXT NOT NULL,
            nonce        TEXT NOT NULL,
            expires      REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            sid          TEXT PRIMARY KEY,
            store_id     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            expires      REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stores_shop ON stores(shop);
        CREATE INDEX IF NOT EXISTS idx_installs_store ON installs(store_id);
        CREATE INDEX IF NOT EXISTS idx_challenges_store ON challenges(store_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_store ON sessions(store_id);
    '''

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
        self.db_path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- Stores ---

    def add_store(self, store_id: str, shop: str,
                  password: str, title: str = '') -> None:
        with self._connect() as conn:
            conn.execute(
                '''INSERT INTO stores (id, shop, password, title)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       shop=excluded.shop,
                       password=excluded.password,
                       title=excluded.title''',
                (store_id, shop, password, title),
            )

    def get_store(self, store_id: str) -> StoreRow | None:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM stores WHERE id=?', (store_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_store_by_shop(self, shop: str) -> StoreRow | None:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM stores WHERE shop=?', (shop,)
            ).fetchone()
        return dict(row) if row else None

    def delete_store(self, store_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute('DELETE FROM stores WHERE id=?', (store_id,))
            return cur.rowcount > 0

    def list_stores(self) -> list[StoreRow]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT id, shop, title FROM stores ORDER BY created_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Installs (candidates) ---

    def add_install(self, candidate: str, store_id: str,
                    shop: str, password: str) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO installs VALUES (?, ?, ?, ?, julianday("now"))',
                (candidate, store_id, shop, password),
            )

    def get_install(self, candidate: str) -> StoreRow | None:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM installs WHERE candidate=?', (candidate,)
            ).fetchone()
        return dict(row) if row else None

    def delete_install(self, candidate: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM installs WHERE candidate=?', (candidate,)
            )
            return cur.rowcount > 0

    def list_installs(self) -> list[StoreRow]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM installs ORDER BY created_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Challenges ---

    def add_challenge(self, sid: str, store_id: str,
                      nonce: str, ttl: int = 300) -> None:
        expires = time.time() + ttl
        with self._connect() as conn:
            self._cleanup_expired(conn)
            conn.execute(
                'INSERT INTO challenges VALUES (?, ?, ?, ?)',
                (sid, store_id, nonce, expires),
            )

    def consume_challenge(self, sid: str) -> StoreRow | None:
        """Извлечь и удалить challenge. Однократное использование."""
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'DELETE FROM challenges WHERE sid=? RETURNING *', (sid,)
            ).fetchone()
        return dict(row) if row else None

    def delete_store_challenges(self, store_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                'DELETE FROM challenges WHERE store_id=?', (store_id,)
            )

    # --- Sessions ---

    def add_session(self, sid: str, store_id: str,
                    user_id: str, ttl: int = 3600) -> None:
        expires = time.time() + ttl
        with self._connect() as conn:
            self._cleanup_expired(conn)
            conn.execute(
                'INSERT INTO sessions VALUES (?, ?, ?, ?)',
                (sid, store_id, user_id, expires),
            )

    def get_session(self, sid: str) -> SessionRow | None:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM sessions WHERE sid=? AND expires>?',
                (sid, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, sid: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM sessions WHERE sid=?', (sid,)
            )
            return cur.rowcount > 0

    def delete_store_sessions(self, store_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                'DELETE FROM sessions WHERE store_id=?', (store_id,)
            )

    def cleanup(self) -> None:
        """Очистить истёкшие challenges и sessions."""
        with self._connect() as conn:
            self._cleanup_expired(conn)

    @staticmethod
    def _cleanup_expired(conn: sqlite3.Connection) -> None:
        now = time.time()
        conn.execute('DELETE FROM challenges WHERE expires<?', (now,))
        conn.execute('DELETE FROM sessions WHERE expires<?', (now,))
