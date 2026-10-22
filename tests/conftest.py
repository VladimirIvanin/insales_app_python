"""Фикстуры для тестов Lifecycle."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

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
    """SQLite Store на временной БД."""
    s = Store(tmp_db_path)
    yield s


@pytest.fixture
def lifecycle(store):
    """Lifecycle с тестовыми credentials, worker'ы НЕ запущены."""
    lc = Lifecycle(
        store=store,
        app_id='test-app',
        app_secret='test-secret-key',
        dev=True,
        allowed_shops={'shop.example', 'another.shop'},
    )
    # Не вызываем lc.start() — тесты делают это явно или работают без workers
    yield lc
    lc.shutdown()


@pytest.fixture
def lifecycle_with_workers(store):
    """Lifecycle с запущенными worker'ами."""
    lc = Lifecycle(
        store=store,
        app_id='test-app',
        app_secret='test-secret-key',
        dev=True,
        allowed_shops={'shop.example', 'another.shop'},
    )
    lc.start()
    yield lc
    lc.shutdown()


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
