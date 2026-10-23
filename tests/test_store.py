"""Тесты схемы БД, миграций и транзакционных операций store."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.db.connection import status, upgrade
from insales.store import Store


@pytest.fixture
def tmp_db():
    """Путь к временному SQLite без схемы."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / 'test.sqlite3'


def test_fresh_db_gets_schema(tmp_db):
    """Новая БД получает схему текущей версии."""
    upgrade(tmp_db)
    store = Store(tmp_db)
    store.add_store('1', 'shop.example', 'pwd', title='Shop')

    row = store.get_store('1')
    assert row is not None
    assert row['shop'] == 'shop.example'


def test_upgrade_idempotent(tmp_db):
    """Повторный upgrade не вызывает ошибок."""
    upgrade(tmp_db)
    upgrade(tmp_db)  # второй раз
    store = Store(tmp_db)
    store.add_store('1', 'shop.example', 'pwd')
    assert store.get_store('1') is not None


def test_upgrade_existing_data_preserved(tmp_db):
    """Upgrade существующей БД с данными не теряет записи."""
    # Создаём старую БД с таблицами (без generation в sessions)
    conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stores (
            id TEXT PRIMARY KEY, shop TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, title TEXT DEFAULT '',
            generation INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS installs (
            candidate TEXT PRIMARY KEY, store_id TEXT NOT NULL,
            shop TEXT NOT NULL, password TEXT NOT NULL,
            generation INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS challenges (
            sid TEXT PRIMARY KEY, store_id TEXT NOT NULL,
            nonce TEXT NOT NULL, expires REAL NOT NULL
        );
        -- sessions БЕЗ generation
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY, store_id TEXT NOT NULL,
            user_id TEXT NOT NULL, expires REAL NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO stores (id, shop, password, title, generation) "
        "VALUES ('42', 'shop.example', 'pwd', 'Old Shop', 1)"
    )
    conn.execute(
        "INSERT INTO sessions (sid, store_id, user_id, expires) "
        "VALUES ('sess1', '42', 'user1', 9999999999)"
    )
    conn.commit()
    conn.close()

    # Upgrade — должен добавить generation в sessions
    upgrade(tmp_db)

    store = Store(tmp_db)
    row = store.get_store('42')
    assert row is not None
    assert row['title'] == 'Old Shop'

    # Сессия должна читаться
    session = store.get_session('sess1')
    assert session is not None
    assert session['store_id'] == '42'


def test_upgrade_status(tmp_db):
    """status показывает корректную версию."""
    st = status(tmp_db)
    for entry in st:
        if entry['version'] == 'current':
            assert entry['applied'] == 0

    upgrade(tmp_db)
    st2 = status(tmp_db)
    for entry in st2:
        if entry['version'] == 'current':
            assert entry['applied'] == 8


def test_generation_monotonic_after_upgrade(tmp_db):
    """generation монотонный после upgrade."""
    upgrade(tmp_db)
    store = Store(tmp_db)
    gen1 = store.next_generation()
    gen2 = store.next_generation()
    assert gen2 > gen1


def test_sessions_generation_column_exists(tmp_db):
    """После upgrade sessions имеет колонку generation."""
    upgrade(tmp_db)
    store = Store(tmp_db)
    store.add_store('42', 'shop.example', 'pwd', generation=5)
    store.add_session('s1', '42', 'user1', ttl=3600, generation=5)

    session = store.get_session('s1')
    assert session is not None
    assert session['generation'] == 5


def test_activate_install_stale_generation(tmp_db):
    """Устаревший generation не активируется через activate_install."""
    upgrade(tmp_db)
    store = Store(tmp_db)

    store.add_store('42', 'shop.example', 'pwd', generation=10)
    store.add_install('old', '42', 'shop.example', 'pwd-old', generation=5)

    ok = store.activate_install('old', 'Old Shop')
    assert ok is False

    row = store.get_store('42')
    assert row['generation'] == 10


def test_uninstall_store_generation_check(tmp_db):
    """uninstall_store проверяет generation."""
    upgrade(tmp_db)
    store = Store(tmp_db)

    store.add_store('42', 'shop.example', 'pwd', generation=1)
    # Неправильный generation
    ok = store.uninstall_store('42', expected_generation=99)
    assert ok is False  # generation не совпал
    assert store.get_store('42') is not None

    # Правильный generation
    ok = store.uninstall_store('42', expected_generation=1)
    assert ok is True
    assert store.get_store('42') is None


def test_consume_install_by_store_password(tmp_db):
    """consume_install_by_store_password удаляет кандидата."""
    upgrade(tmp_db)
    store = Store(tmp_db)

    store.add_install('c1', '42', 'shop.example', 'secret-pwd', generation=1)

    ok = store.consume_install_by_store_password('42', 'wrong-pwd')
    assert ok is False
    assert store.get_install('c1') is not None

    ok = store.consume_install_by_store_password('42', 'secret-pwd')
    assert ok is True
    assert store.get_install('c1') is None


def test_next_generation_within_connection(tmp_db):
    """next_generation работает с переданным connection."""
    upgrade(tmp_db)
    store = Store(tmp_db)

    with store._connect() as conn:  # noqa: SLF001
        gen1 = store.next_generation(conn)
        gen2 = store.next_generation(conn)
        assert gen2 > gen1
