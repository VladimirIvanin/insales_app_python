"""Хранилище данных приложения InSales (SQLite).

Таблицы:
- stores: установленные магазины (credentials, title, generation)
- installs: кандидаты установки
- challenges: одноразовые nonce для autologin
- sessions: пользовательские сессии (привязаны к generation)

Credentials в SQLite не зашифрованы.
Для production требуется encryption at rest.

Миграции схемы накатываются в create_app (factory) или
через `app.db.connection.upgrade()`.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# Импорт app.db.connection выполняется внутри методов для избежания цикла

StoreRow = dict
SessionRow = dict


class StoreError(Exception):
    """Ошибка хранилища (магазин не найден, дубликат и т.д.)."""


class Store:
    """Хранилище данных приложения на SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # upgrade схемы должен быть выполнен до создания Store
        # (через create_app или app.db.connection.upgrade)
        self._chmod_done = False

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        from app.db.connection import connect_raw as _db_connect
        with _db_connect(self.db_path) as conn:
            if not self._chmod_done:
                if self.db_path.exists():
                    self.db_path.chmod(0o600)
                self._chmod_done = True
            yield conn

    # --- Поколение ---

    def next_generation(self, conn: sqlite3.Connection | None = None) -> int:
        """Монотонно возрастающий номер поколения на основе времени.

        Использует int(time.time() * 1000) как стартовую точку.
        Если часы переведены назад — гарантирует рост через MAX.
        """
        now_ms = int(time.time() * 1000)
        if conn is None:
            with self._connect() as conn2:
                return self._ensure_monotonic(conn2, now_ms)
        return self._ensure_monotonic(conn, now_ms)

    @staticmethod
    def _ensure_monotonic(conn: sqlite3.Connection, now_ms: int) -> int:
        last = conn.execute(
            'SELECT COALESCE(MAX(generation), 0) FROM stores'
        ).fetchone()[0]
        last_install = conn.execute(
            'SELECT COALESCE(MAX(generation), 0) FROM installs'
        ).fetchone()[0]
        last_tombstone = conn.execute(
            'SELECT COALESCE(MAX(generation), 0) FROM store_tombstones'
        ).fetchone()[0]
        return max(now_ms, last + 1, last_install + 1, last_tombstone + 1)

    # --- Stores ---

    def add_store(self, store_id: str, shop: str,
                  password: str, title: str = '',
                  generation: int = 0) -> None:
        with self._connect() as conn:
            conn.execute(
                '''INSERT INTO stores (id, shop, password, title, generation)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       shop=excluded.shop,
                       password=excluded.password,
                       title=excluded.title,
                       generation=excluded.generation''',
                (store_id, shop, password, title, generation),
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

    def get_store_shop_and_password(self, store_id: str) -> StoreRow | None:
        """Получить shop и password для store_id (для uninstall auth)."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT id, shop, password, generation FROM stores WHERE id=?',
                (store_id,),
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
                    shop: str, password: str, generation: int = 0) -> None:
        with self._connect() as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO installs
                   (candidate, store_id, shop, password, generation, created_at)
                   VALUES (?, ?, ?, ?, ?, julianday("now"))''',
                (candidate, store_id, shop, password, generation),
            )

    def reserve_generation_and_add_install(self, candidate: str,
                                            store_id: str,
                                            shop: str,
                                            password: str) -> int:
        """Атомарно зарезервировать поколение и вставить кандидата.

        Выполняется в одной write-транзакции (BEGIN IMMEDIATE),
        что исключает race condition при конкурентных установках.

        Перед вставкой удаляет все существующие pending-кандидаты
        с тем же store_id+shop+password. Это исключает ситуацию,
        когда при повторном install callback выживает старший
        кандидат после uninstall (см. P2). Кандидаты с другими
        credentials (другой пароль/другой магазин) не затрагиваются.

        Returns:
            Зарезервированное значение generation.
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            now_ms = int(time.time() * 1000)
            last = conn.execute(
                'SELECT COALESCE(MAX(generation), 0) FROM stores'
            ).fetchone()[0]
            last_install = conn.execute(
                'SELECT COALESCE(MAX(generation), 0) FROM installs'
            ).fetchone()[0]
            last_tombstone = conn.execute(
                'SELECT COALESCE(MAX(generation), 0) FROM store_tombstones'
            ).fetchone()[0]
            generation = max(now_ms, last + 1, last_install + 1, last_tombstone + 1)
            # Удаляем существующие pending с matching credentials
            conn.execute(
                'DELETE FROM installs WHERE store_id=? AND shop=? AND password=?',
                (store_id, shop, password),
            )
            conn.execute(
                '''INSERT OR REPLACE INTO installs
                   (candidate, store_id, shop, password, generation, created_at)
                   VALUES (?, ?, ?, ?, ?, julianday("now"))''',
                (candidate, store_id, shop, password, generation),
            )
            return generation

    def get_install(self, candidate: str) -> StoreRow | None:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM installs WHERE candidate=?', (candidate,)
            ).fetchone()
        return dict(row) if row else None

    def get_install_by_store_id(self, store_id: str) -> StoreRow | None:
        """Получить установку по store_id (для uninstall до активации).

        При нескольких кандидатах возвращает последний (максимальный generation).
        """
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM installs WHERE store_id=? '
                'ORDER BY generation DESC LIMIT 1', (store_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_install_by_store_shop_and_password(
        self, store_id: str, shop: str, password: str
    ) -> StoreRow | None:
        """Получить установку по store_id + shop + password.

        При нескольких кандидатах возвращает последний (максимальный generation).
        Используется для uninstall, чтобы отличить корректный кандидат.
        """
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM installs WHERE store_id=? AND shop=? AND password=? '
                'ORDER BY generation DESC LIMIT 1',
                (store_id, shop, password),
            ).fetchone()
        return dict(row) if row else None

    def delete_install(self, candidate: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM installs WHERE candidate=?', (candidate,)
            )
            return cur.rowcount > 0

    def delete_installs_by_store(self, store_id: str) -> int:
        """Удалить все кандидаты для магазина. Возвращает количество."""
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM installs WHERE store_id=?', (store_id,)
            )
            return cur.rowcount

    def get_latest_pending_install(self, store_id: str, shop: str
                                    ) -> StoreRow | None:
        """Получить самый новый непроверенный кандидат для магазина.

        Возвращает кандидат с максимальным generation
        для указанных store_id + shop.
        """
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM installs WHERE store_id=? AND shop=? '
                'ORDER BY generation DESC LIMIT 1',
                (store_id, shop),
            ).fetchone()
        return dict(row) if row else None

    def count_installs_by_store(self, store_id: str) -> int:
        """Количество непроверенных кандидатов для store_id."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT COUNT(*) AS cnt FROM installs WHERE store_id=?',
                (store_id,),
            ).fetchone()
        return row['cnt'] if row else 0

    def cleanup_stale_candidates(self, store_id: str, max_candidates: int = 5,
                                  ttl: int = 86400 * 7) -> int:
        """Удалить лишних и просроченных кандидатов для магазина.

        Удаляет кандидатов старше ttl секунд (по generation — миллисекунды).
        Если после чистки кандидатов больше max_candidates — оставляет
        только самые новые.

        Возвращает количество удалённых строк.
        """
        threshold_ms = int(time.time() * 1000) - ttl * 1000
        deleted = 0
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Удалить просроченных
            cur = conn.execute(
                'DELETE FROM installs WHERE store_id=? AND generation<?',
                (store_id, threshold_ms),
            )
            deleted += cur.rowcount
            # Удалить лишних (оставить только max_candidates самых новых)
            excess = conn.execute(
                'SELECT COUNT(*) - ? FROM installs WHERE store_id=?',
                (max_candidates, store_id),
            ).fetchone()[0]
            if excess > 0:
                conn.execute(
                    '''DELETE FROM installs WHERE store_id=? AND candidate IN (
                        SELECT candidate FROM installs WHERE store_id=?
                        ORDER BY generation DESC
                        LIMIT -1 OFFSET ?
                    )''',
                    (store_id, store_id, max_candidates),
                )
                deleted += excess
        return deleted

    def activate_install(self, candidate: str, title: str) -> bool:
        """Атомарная активация: проверяет, что кандидат — актуального поколения.

        В одной транзакции:
        1. Читает кандидата
        2. Проверяет tombstone — не был ли этот store_id отменён
           после создания кандидата (защита от B2).
        3. Проверяет, что generation кандидата >= текущего generation магазина
           (предотвращает активацию устаревшего кандидата)
        4. Удаляет старые сессии/challenge
        5. Заменяет/создаёт запись магазина
        6. Удаляет кандидата

        Возвращает True при успешной активации, False если кандидат
        был удалён или устарел (гонка с uninstall или повторной установкой).
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            install = conn.execute(
                'SELECT * FROM installs WHERE candidate=?', (candidate,)
            ).fetchone()
            if install is None:
                return False

            install_gen = install['generation']
            store_id = install['store_id']

            # Проверка tombstone: не был ли магазин отменён после создания кандидата
            tombstone_gen = conn.execute(
                'SELECT COALESCE(MAX(generation), 0) FROM store_tombstones WHERE store_id=?',
                (store_id,),
            ).fetchone()[0]
            if tombstone_gen > 0 and install_gen <= tombstone_gen:
                    conn.execute('DELETE FROM installs WHERE candidate=?',
                                 (candidate,))
                    return False

            # Проверить, что generation кандидата не меньше текущего магазина
            store_row = conn.execute(
                'SELECT generation FROM stores WHERE id=?',
                (store_id,),
            ).fetchone()
            if store_row is not None and install_gen < store_row['generation']:
                # Устаревший кандидат — удаляем, не активируем
                conn.execute('DELETE FROM installs WHERE candidate=?',
                             (candidate,))
                return False

            # Удалить старые сессии/challenge
            conn.execute('DELETE FROM sessions WHERE store_id=?',
                         (install['store_id'],))
            conn.execute('DELETE FROM challenges WHERE store_id=?',
                         (install['store_id'],))
            conn.execute(
                '''INSERT INTO stores (id, shop, password, title, generation)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       shop=excluded.shop,
                       password=excluded.password,
                       title=excluded.title,
                       generation=excluded.generation''',
                (install['store_id'], install['shop'], install['password'],
                 title, install_gen),
            )
            conn.execute('DELETE FROM installs WHERE candidate=?',
                         (candidate,))
            return True

    def consume_install_by_store_password(
        self, store_id: str, password: str
    ) -> bool:
        """Атомарное удаление кандидата по store_id+password для uninstall.

        Используется когда установка ещё не активирована, но пришёл uninstall.
        Удаляет только те кандидаты, у которых пароль совпадает (в WHERE,
        а не только в пост-проверке), что исключает удаление всех кандидатов
        при нескольких установках одного магазина.
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            cur = conn.execute(
                'DELETE FROM installs WHERE store_id=? AND password=?',
                (store_id, password),
            )
            return cur.rowcount > 0

    # --- Единая операция uninstall ---

    def uninstall_store(self, store_id: str,
                        expected_generation: int) -> bool:
        """Атомарное удаление магазина и всех связанных данных.

        Удаляет store, sessions, challenges, installs для данного store_id
        в одной write-транзакции, но только если generation совпадает
        (защита от race condition при переустановке).

        Фиксирует tombstone (store_id, generation), чтобы отклонить
        запоздалую активацию кандидата (см. B2). Удаляет installs всех
        поколений — после uninstall ни один кандидат не должен
        активироваться.

        Возвращает True, если магазин был удалён.
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT generation FROM stores WHERE id=?', (store_id,)
            ).fetchone()
            if row is None:
                return False
            if row['generation'] != expected_generation:
                # Магазин уже переустановлен с новым generation
                return False

            conn.execute('DELETE FROM sessions WHERE store_id=?', (store_id,))
            conn.execute('DELETE FROM challenges WHERE store_id=?', (store_id,))
            conn.execute('DELETE FROM installs WHERE store_id=?', (store_id,))
            conn.execute('DELETE FROM stores WHERE id=?', (store_id,))
            conn.execute(
                'INSERT OR IGNORE INTO store_tombstones (store_id, generation) '
                'VALUES (?, ?)',
                (store_id, expected_generation),
            )
            return True

    def uninstall_store_or_pending_install(
        self, store_id: str, shop: str, password: str
    ) -> bool:
        """Атомарная отмена: удалить active store ИЛИ pending install.

        В одной write-транзакции:
        1. Пытается найти active store по store_id с проверкой shop.
           Если найден и пароль совпадает — удаляет всё (store, sessions,
           challenges, installs).
        2. Если active store найден, но пароль не совпадает (другой
           установщик) — удаляет все pending installs с matching
           credentials. Active store не трогается.
        3. Если active store отсутствует — удаляет все pending installs
           по store_id + shop + password.

        Возвращает True при успешном удалении.
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')

            # Шаг 1: проверяем active store
            store_row = conn.execute(
                'SELECT id, shop, password, generation FROM stores WHERE id=?',
                (store_id,),
            ).fetchone()
            if store_row is not None:
                if store_row['shop'] != shop:
                    return False
                from insales.crypto import secure_compare
                if secure_compare(password, store_row['password']):
                    # Active store найден с совпадающим паролем — удаляем всё.
                    conn.execute('DELETE FROM sessions WHERE store_id=?', (store_id,))
                    conn.execute('DELETE FROM challenges WHERE store_id=?', (store_id,))
                    conn.execute('DELETE FROM installs WHERE store_id=?', (store_id,))
                    conn.execute('DELETE FROM stores WHERE id=?', (store_id,))
                    conn.execute(
                        'INSERT OR IGNORE INTO store_tombstones (store_id, generation) '
                        'VALUES (?, ?)',
                        (store_id, store_row['generation']),
                    )
                    return True
                # Active store есть, но пароль не совпал (другой установщик).
                # Удаляем все pending installs с matching credentials.
                cur = conn.execute(
                    'DELETE FROM installs WHERE store_id=? AND shop=? AND password=?',
                    (store_id, shop, password),
                )
                return cur.rowcount > 0

            # Шаг 2: active store нет — ищем и удаляем все pending installs
            cur = conn.execute(
                'DELETE FROM installs WHERE store_id=? AND shop=? AND password=?',
                (store_id, shop, password),
            )
            return cur.rowcount > 0

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
                    user_id: str, ttl: int = 3600,
                    generation: int = 0) -> None:
        expires = time.time() + ttl
        with self._connect() as conn:
            self._cleanup_expired(conn)
            conn.execute(
                'INSERT INTO sessions (sid, store_id, user_id, expires, generation) '
                'VALUES (?, ?, ?, ?, ?)',
                (sid, store_id, user_id, expires, generation),
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
