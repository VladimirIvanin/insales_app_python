"""Фикстуры для тестов Lifecycle."""
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
    return Store(tmp_db_path)


@pytest.fixture
def lifecycle(store):
    """Lifecycle с тестовыми credentials."""
    return Lifecycle(
        store=store,
        app_id='test-app',
        app_secret='test-secret-key',
        dev=True,
    )


@pytest.fixture
def preinstalled_store(store):
    """Предустановленный магазин в БД."""
    good_password = store_password(
        VECTORS['install']['install_token'],
        VECTORS['install']['app_secret'],
    )
    store.add_store(
        store_id='42',
        shop='shop.example',
        password=good_password,
        title='Test Shop',
    )
    return store, good_password
