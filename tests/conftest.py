"""Фикстуры для тестов."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.db.connection import upgrade as db_upgrade
from insales.crypto import store_password
from insales.lifecycle import Lifecycle
from insales.store import Store

VECTORS = json.loads(
    Path(__file__).with_name('protocol-vectors.json').read_text()
)


@pytest.fixture
def tmp_db_path():
    """Временный путь к SQLite для каждого теста."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / 'test.sqlite3'


@pytest.fixture
def store(tmp_db_path):
    """SQLite Store на временной БД (схема накатывается явно)."""
    db_upgrade(tmp_db_path)
    s = Store(tmp_db_path)
    yield s


@pytest.fixture
def lifecycle(store):
    """Lifecycle с тестовыми credentials, без worker'ов."""
    lc = Lifecycle(
        store=store,
        app_id='test-app',
        app_secret='test-secret-key',
        dev=True,
        allowed_shops={'shop.example', 'another.shop'},
    )
    yield lc


@pytest.fixture
def preinstalled_store(store, lifecycle):
    """Предустановленный магазин в БД.

    Возвращает (store, raw_token, good_password).
    """
    raw_token = VECTORS['install']['install_token']
    good_password = store_password(
        raw_token,
        VECTORS['install']['app_secret'],
    )
    store.add_store(
        store_id='42',
        shop='shop.example',
        password=good_password,
        title='Test Shop',
    )
    return store, raw_token, good_password
