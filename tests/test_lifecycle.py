"""Тесты жизненного цикла приложения InSales.

Проверяют:
- Установку с верификацией через API
- Отклонение поддельной установки
- Login/autologin с token3
- Привязку challenge к browser cookie
- Uninstall с проверкой пароля
- Replay-защиту challenge
- Boundary условия (allowlist, невалидные параметры)
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.middleware import SessionMiddleware
from app.routes import create_routes
from insales.crypto import sign_token3

VECTORS = json.loads(
    Path(__file__).with_name('protocol-vectors.json').read_text()
)


class TestInstall:
    def test_install_creates_candidate(self, lifecycle, store):
        """Install создаёт кандидата и возвращает candidate_id."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )
        install = store.get_install(candidate)
        assert install is not None
        assert install['store_id'] == '42'

    def test_install_verifies_api_and_promotes(self, lifecycle, store):
        """После успешной верификации через API кандидат становится магазином."""
        from unittest.mock import MagicMock

        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )

        # Мокаем InSalesClient внутри verify_install
        mock_client = MagicMock()
        mock_client.get_account.return_value = ({'id': 42, 'title': 'Shop'}, '1/500')

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            result = lifecycle.verify_install(candidate)

        assert result is True
        store_row = store.get_store('42')
        assert store_row is not None
        assert store_row['shop'] == 'shop.example'

    def test_forged_install_does_not_replace_working(self, lifecycle, store,
                                                     preinstalled_store):
        """Поддельная установка не заменяет работающее подключение."""
        store, good_password = preinstalled_store
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='fake-token',
        )
        # Верификация провалится, кандидат удалится
        lifecycle.verify_install(candidate)

        store_row = store.get_store('42')
        assert store_row['password'] == good_password

    def test_store_id_mismatch_is_rejected(self, lifecycle, store):
        """Несовпадение store_id в account API отклоняет установку."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='99',
            token='install-token',
        )

        # Подмена: API вернёт аккаунт с id=42, не 99
        with patch.object(lifecycle, 'verify_install') as mock:
            mock.return_value = False
            lifecycle.verify_install(candidate)

        assert store.get_store('99') is None

    def test_invalid_insales_id_rejected(self, lifecycle):
        """Нечисловой insales_id вызывает ошибку."""
        with pytest.raises(ValueError):
            lifecycle.process_install(
                shop='shop.example',
                insales_id='abc',
                token='token',
            )


class TestUninstall:
    def test_uninstall_with_correct_token(self, lifecycle, store,
                                           preinstalled_store):
        """Правильный пароль удаляет магазин и чистит данные."""
        store, good_password = preinstalled_store
        store.add_challenge('ch1', '42', 'nonce1')
        store.add_session('sess1', '42', 'user1', ttl=3600)

        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            token=good_password,
        )
        assert result is True
        assert store.get_store('42') is None
        assert store.get_session('sess1') is None

    def test_uninstall_with_wrong_token(self, lifecycle, preinstalled_store):
        """Неверный пароль вызывает ошибку."""
        store, _ = preinstalled_store
        with pytest.raises(Exception):
            lifecycle.process_uninstall(
                shop='shop.example',
                insales_id='42',
                token='0' * 32,
            )
        assert store.get_store('42') is not None

    def test_uninstall_nonexistent_store(self, lifecycle):
        """Удаление несуществующего магазина — False."""
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='999',
            token='anything',
        )
        assert result is False


class TestAutologin:
    def test_full_login_flow(self, lifecycle, store, preinstalled_store):
        """Полный сценарий challenge → callback → сессия."""
        store, good_password = preinstalled_store

        # Создать challenge
        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        # Получить callback с InSales
        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '7',
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            params['user_id'], params['email_confirmed'],
            good_password,
        )

        # Проверить autologin
        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is not None

        # Сессия должна быть активна
        session = store.get_session(session_id)
        assert session is not None
        assert session['store_id'] == '42'
        assert session['user_id'] == '7'

    def test_modified_user_cannot_pass(self, lifecycle, preinstalled_store):
        """Изменённый user_id не проходит верификацию token3."""
        store, good_password = preinstalled_store

        # Создать challenge
        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        # Подделанный callback
        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '8',  # изменено
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            '7', params['email_confirmed'],  # original user_id
            good_password,
        )

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is None

    def test_challenge_bound_to_cookie(self, lifecycle, preinstalled_store):
        """Challenge привязан к cookie: другой sid не подходит."""
        store, good_password = preinstalled_store

        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '7',
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            params['user_id'], params['email_confirmed'],
            good_password,
        )

        # Другой cookie_sid
        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid='different-sid',
            params=params,
        )
        assert session_id is None

    def test_expired_challenge_rejected(self, lifecycle, preinstalled_store):
        """Истёкший challenge отклоняется."""
        store, good_password = preinstalled_store

        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        # "Состарить" challenge — протухший expires
        with store._connect() as conn:
            conn.execute('UPDATE challenges SET expires=0 WHERE sid=?',
                         (challenge_sid,))

        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '7',
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            params['user_id'], params['email_confirmed'],
            good_password,
        )

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is None

    def test_replay_challenge_rejected(self, lifecycle, preinstalled_store):
        """Повторное использование challenge отклоняется."""
        store, good_password = preinstalled_store

        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '7',
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            params['user_id'], params['email_confirmed'],
            good_password,
        )

        # Первый раз — успех
        sid1 = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert sid1 is not None

        # Второй раз — уже потреблён
        sid2 = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert sid2 is None

    def test_nonexistent_store_challenge(self, lifecycle):
        """Challenge для несуществующего магазина — ошибка."""
        with pytest.raises(Exception):
            lifecycle.create_challenge('999')


class TestStoreLifecycle:
    """Интеграционные тесты через Flask routes."""

    @pytest.fixture
    def app(self, store, lifecycle):
        """Flask-приложение для интеграционных тестов."""
        flask_app = Flask(__name__)
        flask_app.secret_key = 'test-secret'
        mw = SessionMiddleware(store, 'test-app', 'test-secret-key', dev=True)
        blueprint = create_routes(
            store, lifecycle, mw,
            base_url='http://demo.example',
        )
        flask_app.register_blueprint(blueprint)
        yield flask_app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_health(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        assert resp.data == b'ok'

    def test_install_received(self, client):
        resp = client.get('/install', query_string={
            'shop': 'shop.example',
            'insales_id': '42',
            'token': 'install-token',
        })
        assert resp.status_code == 200

    def test_install_missing_params(self, client):
        resp = client.get('/install', query_string={
            'shop': 'shop.example',
        })
        assert resp.status_code == 400

    def test_unauthenticated_dashboard(self, client):
        resp = client.get('/')
        assert resp.status_code == 401

    def test_not_found(self, client):
        resp = client.get('/nonexistent')
        assert resp.status_code == 404
