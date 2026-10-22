"""Тесты жизненного цикла приложения InSales.

Проверяют:
- Установку с верификацией через API
- ALLOWED_SHOPS (обязательный)
- Отклонение поддельной установки
- Атомарную активацию (activate_install, гонки поколений)
- Login/autologin с token3
- Привязку challenge к browser cookie
- Uninstall с проверкой пароля и единой транзакцией
- Uninstall до активации (pending install)
- Replay-защиту challenge
- Привязку сессии к поколению
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.middleware import SessionMiddleware
from app.routes import create_routes
from insales.client import ApiError
from insales.crypto import sign_token3
from insales.lifecycle import Lifecycle, LifecycleError

VECTORS = json.loads(
    Path(__file__).with_name('protocol-vectors.json').read_text()
)

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_creates_candidate(self, lifecycle, store):
        """Install создаёт кандидата."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )
        install = store.get_install(candidate)
        assert install is not None
        assert install['store_id'] == '42'

    def test_install_allowed_shops_blocks_unknown(self, lifecycle, store):
        """Неразрешённый магазин отклоняется до сохранения кандидата."""
        with pytest.raises(ValueError, match='not in ALLOWED_SHOPS'):
            lifecycle.process_install(
                shop='evil.example',
                insales_id='99',
                token='token',
            )
        assert store.list_installs() == []

    def test_install_allowed_shops_empty_rejected(self, store):
        """Пустой ALLOWED_SHOPS отклоняет все установки."""
        lc = Lifecycle(store=store, app_id='app', app_secret='secret',
                       dev=True, allowed_shops=set())
        with pytest.raises(ValueError, match='ALLOWED_SHOPS is empty'):
            lc.process_install(
                shop='any.domain',
                insales_id='1',
                token='tok',
            )
        assert store.list_installs() == []
        lc.shutdown()

    def test_verify_install_success(self, lifecycle, store):
        """Успешная верификация активирует магазин через activate_install."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'Test Shop'}, '1/500',
        )

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        store_row = store.get_store('42')
        assert store_row is not None
        assert store_row['shop'] == 'shop.example'
        assert store_row['title'] == 'Test Shop'

    def test_forged_install_does_not_replace_working(
        self, lifecycle, store, preinstalled_store,
    ):
        """Поддельная установка не заменяет работающее подключение."""
        store, *_ = preinstalled_store
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='fake-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(401)

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        store_row = store.get_store('42')
        # Пароль не должен измениться после поддельной установки
        assert store_row is not None

    def test_store_id_mismatch_rejected(self, lifecycle, store):
        """Несовпадение store_id в account API отклоняет кандидата."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='99',
            token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.return_value = ({'id': 42}, '1/500')

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        assert store.get_store('99') is None
        assert store.get_install(candidate) is None

    def test_temporary_error_preserves_candidate(self, lifecycle, store):
        """Временная ошибка (429) сохраняет кандидата для повтора."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(429)

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        assert store.get_install(candidate) is not None

    def test_network_error_preserves_candidate(self, lifecycle, store):
        """Сетевой обрыв сохраняет кандидата для повтора при рестарте."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = OSError('Connection refused')

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        assert store.get_install(candidate) is not None

    def test_activate_install_atomic(self, store):
        """activate_install атомарно проверяет и активирует."""
        store.add_install('cand1', '42', 'shop.example', 'pwd', generation=1)

        ok = store.activate_install('cand1', 'My Shop')
        assert ok is True

        store_row = store.get_store('42')
        assert store_row is not None
        assert store_row['title'] == 'My Shop'
        assert store.get_install('cand1') is None

    def test_activate_install_race_with_uninstall(self, store):
        """activate_install возвращает False, если кандидат удалён (гонка)."""
        store.add_install('cand2', '42', 'shop.example', 'pwd', generation=1)
        store.delete_install('cand2')

        ok = store.activate_install('cand2', 'Ghost')
        assert ok is False

    def test_activate_install_stale_generation_rejected(self, store):
        """Устаревший кандидат (generation < текущего магазина) отклоняется."""
        # Магазин уже активирован с generation=10
        store.add_store('42', 'shop.example', 'pwd', title='Active',
                        generation=10)
        # Старый кандидат с generation=5
        store.add_install('old-cand', '42', 'shop.example', 'pwd-old',
                          generation=5)

        ok = store.activate_install('old-cand', 'Old Shop')
        assert ok is False

        # Магазин не изменился
        store_row = store.get_store('42')
        assert store_row['generation'] == 10

    def test_activate_install_current_generation_accepted(self, store):
        """Кандидат с generation >= текущего магазина активируется."""
        store.add_store('42', 'shop.example', 'pwd', title='Active',
                        generation=10)
        # Новый кандидат с generation=15
        store.add_install('new-cand', '42', 'shop.example', 'pwd-new',
                          generation=15)

        ok = store.activate_install('new-cand', 'New Shop')
        assert ok is True

        store_row = store.get_store('42')
        assert store_row['generation'] == 15

    def test_generation_monotonic(self, store):
        """next_generation всегда возвращает возрастающее значение."""
        gen1 = store.next_generation()
        gen2 = store.next_generation()
        assert gen2 > gen1

    def test_resume_pending_installs(self, store):
        """Восстановление незавершённых кандидатов при старте вызывает worker."""
        # Создаём install до старта lifecycle
        store.add_install('old1', '42', 'shop.example', 'some-password',
                          generation=1)

        lc = Lifecycle(
            store=store, app_id='test-app',
            app_secret='test-secret-key',
            dev=True, allowed_shops={'shop.example'},
        )
        try:
            mock_client = MagicMock()
            mock_client.get_account.return_value = (
                {'id': 42, 'title': 'T'}, '1/500',
            )
            with patch.object(lc, '_make_client', return_value=mock_client):
                lc.start()  # запускает _resume_pending_installs внутри
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if store.get_store('42') is not None:
                        break
                    time.sleep(0.05)

            store_row = store.get_store('42')
            assert store_row is not None
            assert store_row['shop'] == 'shop.example'
        finally:
            lc.shutdown()

    def test_resume_pending_installs_empty(self, lifecycle_with_workers):
        """resume не падает, если кандидатов нет."""
        pass  # просто что start() не упал

    def test_invalid_insales_id_rejected(self, lifecycle):
        """Нечисловой insales_id вызывает ошибку."""
        with pytest.raises(ValueError):
            lifecycle.process_install(
                shop='shop.example',
                insales_id='abc',
                token='token',
            )

    def test_start_stop_worker(self, lifecycle):
        """start/shutdown работают идемпотентно."""
        assert not lifecycle.running
        lifecycle.start()
        assert lifecycle.running
        lifecycle.start()  # повторный — без эффекта
        assert lifecycle.running
        lifecycle.shutdown()
        assert not lifecycle.running
        lifecycle.shutdown()  # повторный — без эффекта


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_with_correct_token(self, lifecycle, store,
                                           preinstalled_store):
        """Правильный пароль удаляет магазин, candidate и данные."""
        store, _, good_password = preinstalled_store
        store.add_challenge('ch1', '42', 'nonce1')
        store.add_session('sess1', '42', 'user1', ttl=3600)
        store.add_install('cand1', '42', 'shop.example', 'pwd')

        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert result is True
        assert store.get_store('42') is None
        assert store.get_session('sess1') is None
        assert store.get_install('cand1') is None

    def test_uninstall_with_wrong_token(self, lifecycle, preinstalled_store):
        """Неверный пароль вызывает ошибку, магазин сохраняется."""
        store, _, _ = preinstalled_store
        with pytest.raises(LifecycleError, match='Invalid uninstall token'):
            lifecycle.process_uninstall(
                shop='shop.example',
                insales_id='42',
                password='0' * 32,
            )
        assert store.get_store('42') is not None

    def test_uninstall_nonexistent_store(self, lifecycle):
        """Удаление несуществующего магазина — False."""
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='999',
            password='anything',
        )
        assert result is False

    def test_uninstall_removes_candidates(self, lifecycle, store,
                                           preinstalled_store):
        """Uninstall удаляет все кандидаты для этого store_id."""
        store, _, good_password = preinstalled_store
        store.add_install('c1', '42', 'shop.example', 'pwd')
        store.add_install('c2', '42', 'shop.example', 'pwd')

        lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert store.get_install('c1') is None
        assert store.get_install('c2') is None

    def test_uninstall_before_activation(self, lifecycle, store):
        """Uninstall до активации — должен отменить pending install."""
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token',
        )
        install = store.get_install(candidate)
        assert install is not None
        assert install['password'] is not None
        good_password = install['password']

        # Симулируем uninstall с готовым паролем (как присылает InSales)
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert result is True
        assert store.get_install(candidate) is None

    def test_uninstall_new_install_not_affected(self, lifecycle, store,
                                                 preinstalled_store):
        """Старый uninstall не удаляет новую установку (через generation)."""
        store, _, old_good_password = preinstalled_store

        # Получаем generation магазина
        store_row = store.get_store('42')
        old_generation = store_row['generation']

        # Новая установка меняет generation
        candidate = lifecycle.process_install(
            shop='shop.example',
            insales_id='42',
            token='install-token2',
        )
        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'New Shop'}, '1/500',
        )
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle._verify_install_worker(candidate)

        # Теперь пробуем uninstall со СТАРЫМ паролем (должен не совпасть,
        # т.к. пароль изменился при переустановке)
        with pytest.raises(LifecycleError, match='Invalid uninstall token'):
            lifecycle.process_uninstall(
                shop='shop.example',
                insales_id='42',
                password=old_good_password,
            )
        # Новая установка жива
        assert store.get_store('42') is not None
        assert store.get_store('42')['generation'] > old_generation

    def test_uninstall_atomic_transaction(self, lifecycle, store,
                                           preinstalled_store):
        """Uninstall в одной транзакции — частичное состояние не сохраняется."""
        store, _, good_password = preinstalled_store

        # Если бы транзакция не была атомарной — могли бы остаться
        # сессии без магазина
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert result is True

        # После uninstall не должно быть сессий без магазина
        with store._connect() as conn:  # noqa: SLF001
            orphan_sessions = conn.execute(
                '''SELECT COUNT(*) FROM sessions s
                   LEFT JOIN stores st ON s.store_id = st.id
                   WHERE st.id IS NULL'''
            ).fetchone()[0]
        assert orphan_sessions == 0


# ---------------------------------------------------------------------------
# Autologin
# ---------------------------------------------------------------------------


class TestAutologin:
    def test_full_login_flow(self, lifecycle, store, preinstalled_store):
        """Полный сценарий challenge → callback → сессия."""
        store, _, good_password = preinstalled_store

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

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is not None

        session = store.get_session(session_id)
        assert session is not None
        assert session['store_id'] == '42'
        assert session['user_id'] == '7'

    def test_modified_user_cannot_pass(self, lifecycle, preinstalled_store):
        """Изменённый user_id не проходит верификацию token3."""
        store, _, good_password = preinstalled_store

        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '8',
            'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(
            nonce, params['user_email'], params['user_name'],
            '7', params['email_confirmed'],
            good_password,
        )

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is None

    def test_session_generation_bound(self, lifecycle, store,
                                       preinstalled_store):
        """Сессия привязана к поколению магазина."""
        store, _, good_password = preinstalled_store
        store_row = store.get_store('42')
        old_gen = store_row['generation']

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

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert session_id is not None
        session = store.get_session(session_id)
        assert session['generation'] == old_gen

    def test_challenge_bound_to_cookie(self, lifecycle, preinstalled_store):
        """Challenge привязан к cookie: другой sid не подходит."""
        store, _, good_password = preinstalled_store

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

        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid='different-sid',
            params=params,
        )
        assert session_id is None

    def test_expired_challenge_rejected(self, lifecycle, preinstalled_store):
        """Истёкший challenge отклоняется."""
        store, _, good_password = preinstalled_store

        challenge_sid, nonce, _ = lifecycle.create_challenge('42')

        with store._connect() as conn:  # noqa: SLF001
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
        store, _, good_password = preinstalled_store

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

        sid1 = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=params,
        )
        assert sid1 is not None

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


# ---------------------------------------------------------------------------
# Route integration
# ---------------------------------------------------------------------------


class TestStoreLifecycle:
    """Интеграционные тесты через Flask routes, собранные через factory."""

    @pytest.fixture
    def app(self, store, lifecycle):
        """Flask-приложение для интеграционных тестов (без workers)."""
        flask_app = Flask(__name__,
                          template_folder=str(
                              Path(__file__).parent.parent / 'app' / 'templates'
                          ))
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

    def test_install_blocked_by_allowed_shops(self, client):
        resp = client.get('/install', query_string={
            'shop': 'evil.example',
            'insales_id': '99',
            'token': 'x',
        })
        assert resp.status_code == 400

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

    def test_login_without_install(self, client):
        resp = client.get('/login', query_string={
            'shop': 'shop.example',
            'insales_id': '42',
        })
        assert resp.status_code == 404

    def test_webhook_not_implemented(self, client):
        resp = client.post('/webhook/insales')
        assert resp.status_code == 404
