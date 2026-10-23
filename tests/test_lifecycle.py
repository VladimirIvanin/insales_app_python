"""Тесты жизненного цикла приложения InSales.

Проверяют:
- Установку (сохранение кандидата без сетевого вызова)
- Проверку кандидата при входе (verify_pending_install)
- ALLOWED_SHOPS (обязательный)
- Login/autologin с token3
- Привязку challenge к browser cookie
- Uninstall с проверкой пароля и единой транзакцией
- Uninstall до активации (pending install)
- Replay-защиту challenge
- Привязку сессии к поколению
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from insales.client import ApiError
from insales.crypto import sign_token3
from insales.lifecycle import Lifecycle, LifecycleError
from insales.transport import TransportError

VECTORS = json.loads(
    Path(__file__).with_name('protocol-vectors.json').read_text()
)

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_creates_candidate(self, lifecycle, store):
        """Install создаёт кандидата без сетевого вызова."""
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
        # Все кандидаты = 0
        count = store.count_installs_by_store('99')
        assert count == 0

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
        assert store.count_installs_by_store('1') == 0

    def test_invalid_insales_id_rejected(self, lifecycle):
        """Нечисловой insales_id вызывает ошибку."""
        with pytest.raises(ValueError):
            lifecycle.process_install(
                shop='shop.example',
                insales_id='abc',
                token='token',
            )

    def test_repeat_install_same_credentials(self, lifecycle, store):
        """Повторный install с теми же credentials сохраняет кандидата и поколение."""
        c1 = lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )
        assert store.get_install(c1) is not None

        c2 = lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )
        assert c2 == c1
        assert store.get_install(c1) is not None
        assert store.get_install(c2) is not None

    def test_install_duplicate_credentials_no_extra_candidates(
        self, lifecycle, store,
    ):
        """Повторный install с теми же данными не создаёт дубликат кандидата."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )
        assert store.count_installs_by_store('42') == 1

    def test_install_no_api_call(self, lifecycle, store):
        """Install callback не вызывает API."""
        with patch.object(lifecycle, '_make_client') as mock:
            candidate = lifecycle.process_install(
                shop='shop.example', insales_id='42', token='tok',
            )
        mock.assert_not_called()
        assert store.get_install(candidate) is not None


# ---------------------------------------------------------------------------
# Verify install on first login
# ---------------------------------------------------------------------------


class TestVerifyInstall:
    def test_verify_success_activates_store(self, lifecycle, store):
        """Успешная проверка API активирует магазин."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )
        assert store.get_store('42') is None  # ещё не активирован

        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'Test Shop'}, '1/500',
        )
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            result = lifecycle.verify_pending_install('42', 'shop.example')

        assert result == '42'
        store_row = store.get_store('42')
        assert store_row is not None
        assert store_row['shop'] == 'shop.example'
        assert store_row['title'] == 'Test Shop'

    def test_verify_store_id_mismatch_deletes_candidate(self, lifecycle, store):
        """Несовпадение store_id отклоняет и удаляет кандидата."""
        lifecycle.process_install(
            shop='shop.example', insales_id='99', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.return_value = ({'id': 42}, '1/500')
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with pytest.raises(LifecycleError, match='Store ID mismatch'):
                lifecycle.verify_pending_install('99', 'shop.example')

        assert store.get_store('99') is None
        assert store.count_installs_by_store('99') == 0

    def test_verify_temporary_api_error_preserves_candidate(self, lifecycle, store):
        """Временная ошибка (429) сохраняет кандидата для повтора."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(429)
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with pytest.raises(LifecycleError, match='Temporary API error'):
                lifecycle.verify_pending_install('42', 'shop.example')

        # Кандидат сохранён
        assert store.count_installs_by_store('42') == 1

    def test_verify_temporary_api_error_5xx_preserves_candidate(self, lifecycle, store):
        """Временная ошибка 5xx сохраняет кандидата для повтора."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(503)
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with pytest.raises(LifecycleError, match='Temporary API error'):
                lifecycle.verify_pending_install('42', 'shop.example')

        assert store.count_installs_by_store('42') == 1

    def test_verify_network_error_preserves_candidate(self, lifecycle, store):
        """Сетевой обрыв сохраняет кандидата для повтора."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = TransportError('Connection failed')
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with pytest.raises(LifecycleError, match='Temporary API error'):
                lifecycle.verify_pending_install('42', 'shop.example')

        assert store.count_installs_by_store('42') == 1

    def test_verify_403_deletes_candidate(self, lifecycle, store):
        """Достоверно неверные credentials (403) удаляют кандидата."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(403)
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with pytest.raises(LifecycleError, match='API rejected'):
                lifecycle.verify_pending_install('42', 'shop.example')

        assert store.count_installs_by_store('42') == 0

    def test_verify_no_candidate_returns_none(self, lifecycle, store):
        """Нет кандидата — возвращает None."""
        result = lifecycle.verify_pending_install('42', 'shop.example')
        assert result is None

    def test_verify_candidate_deleted_during_request(self, lifecycle, store):
        """Кандидат удалён во время API-запроса (гонка с uninstall)."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'Shop'}, '1/500',
        )

        def activate_side_effect(candidate, title):
            # Симулируем uninstall до активации
            store.delete_install(candidate)
            return False

        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            with patch.object(store, 'activate_install', side_effect=activate_side_effect):
                result = lifecycle.verify_pending_install('42', 'shop.example')

        assert result is None  # не активирован, но и не ошибка
        assert store.get_store('42') is None

    def test_forged_install_does_not_replace_working(
        self, lifecycle, store, preinstalled_store,
    ):
        """Поддельная установка не заменяет работающее подключение."""
        store, _, _ = preinstalled_store
        # Уже есть активное подключение
        store_row_before = store.get_store('42')
        assert store_row_before is not None

        # Добавляем поддельного кандидата с другим паролем
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='fake-token',
        )

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ApiError(403)
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            # При 403 с активным магазином возвращается его store_id
            result = lifecycle.verify_pending_install('42', 'shop.example')

        assert result == '42'  # активное подключение сохранено
        store_row = store.get_store('42')
        assert store_row is not None
        assert store_row['password'] == store_row_before['password']

    def test_late_install_after_days(self, lifecycle, store):
        """Кандидат сохраняется несколько дней, переживает перезапуск."""
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token',
        )

        # "Перезапуск" — новый lifecycle с тем же store
        lc2 = Lifecycle(
            store=store, app_id='test-app',
            app_secret='test-secret-key',
            dev=True, allowed_shops={'shop.example'},
        )

        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'Late Shop'}, '1/500',
        )
        with patch.object(lc2, '_make_client', return_value=mock_client):
            result = lc2.verify_pending_install('42', 'shop.example')

        assert result == '42'
        assert store.get_store('42') is not None
        assert store.get_store('42')['title'] == 'Late Shop'


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
        """Неверный пароль — False, магазин сохраняется."""
        store, _, _ = preinstalled_store
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password='0' * 32,
        )
        assert result is False
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
        good_password = install['password']

        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert result is True
        assert store.get_install(candidate) is None

    def test_uninstall_new_install_not_affected(self, lifecycle, store,
                                                 preinstalled_store):
        """Старый uninstall не удаляет новую установку (через смену пароля)."""
        store, _, old_good_password = preinstalled_store
        store_row = store.get_store('42')
        old_generation = store_row['generation']

        # Активируем новую установку через verify
        lifecycle.process_install(
            shop='shop.example', insales_id='42', token='install-token2',
        )
        mock_client = MagicMock()
        mock_client.get_account.return_value = (
            {'id': 42, 'title': 'New Shop'}, '1/500',
        )
        with patch.object(lifecycle, '_make_client', return_value=mock_client):
            lifecycle.verify_pending_install('42', 'shop.example')

        # Старый пароль больше не подходит
        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=old_good_password,
        )
        assert result is False
        # Новая установка жива
        assert store.get_store('42') is not None
        assert store.get_store('42')['generation'] > old_generation

    def test_uninstall_atomic_transaction(self, lifecycle, store,
                                           preinstalled_store):
        """Uninstall в одной транзакции — частичное состояние не сохраняется."""
        store_obj, _, good_password = preinstalled_store
        store_obj.add_session('test-session-1', '42', 'user-1')
        store_obj.add_challenge('test-challenge-1', '42', 'nonce-1')

        assert store_obj.get_session('test-session-1') is not None
        with store_obj._connect() as conn:  # noqa: SLF001
            challenge = conn.execute(
                'SELECT * FROM challenges WHERE sid=?', ('test-challenge-1',)
            ).fetchone()
        assert challenge is not None

        result = lifecycle.process_uninstall(
            shop='shop.example',
            insales_id='42',
            password=good_password,
        )
        assert result is True
        assert store_obj.get_store('42') is None
        assert store_obj.get_session('test-session-1') is None

        # Нет orphan sessions
        with store_obj._connect() as conn:  # noqa: SLF001
            orphan_sessions = conn.execute(
                '''SELECT COUNT(*) FROM sessions s
                   LEFT JOIN stores st ON s.store_id = st.id
                   WHERE st.id IS NULL'''
            ).fetchone()[0]
        assert orphan_sessions == 0

        # Нет orphan challenges
        with store_obj._connect() as conn:  # noqa: SLF001
            orphan_challenges = conn.execute(
                '''SELECT COUNT(*) FROM challenges c
                   LEFT JOIN stores st ON c.store_id = st.id
                   WHERE st.id IS NULL'''
            ).fetchone()[0]
        assert orphan_challenges == 0

    def test_uninstall_idempotent(self, lifecycle, store, preinstalled_store):
        """Повторный uninstall с правильным паролем — False (уже удалён)."""
        store, _, good_password = preinstalled_store

        r1 = lifecycle.process_uninstall(
            shop='shop.example', insales_id='42', password=good_password,
        )
        assert r1 is True

        r2 = lifecycle.process_uninstall(
            shop='shop.example', insales_id='42', password=good_password,
        )
        assert r2 is False  # уже не существует


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

        # Передаём неверный challenge_sid — не совпадает с сохранённым
        session_id = lifecycle.verify_autologin(
            challenge_sid='wrong-sid',
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
            params=params,
        )
        assert sid1 is not None

        sid2 = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            params=params,
        )
        assert sid2 is None

    def test_nonexistent_store_challenge(self, lifecycle):
        """Challenge для несуществующего магазина — LifecycleError."""
        with pytest.raises(LifecycleError, match='Store not installed'):
            lifecycle.create_challenge('999')

    def test_empty_challenge_sid_rejected(self, lifecycle, preinstalled_store):
        """Пустой challenge_sid отклоняется."""
        store, _, good_password = preinstalled_store

        params = {
            'user_email': 'test@example.com',
            'user_name': 'Test User',
            'user_id': '7',
            'email_confirmed': 'true',
        }
        # Не создаём challenge — пустой sid
        session_id = lifecycle.verify_autologin(
            challenge_sid='',
            params=params,
        )
        assert session_id is None


# ---------------------------------------------------------------------------
# Route integration
# ---------------------------------------------------------------------------


class TestStoreLifecycle:
    """Интеграционные тесты через Flask routes, собранные через factory."""

    @pytest.fixture
    def app(self, tmp_path):
        """Flask-приложение для интеграционных тестов через create_app с чистой БД."""
        from app.config import Settings
        from app.factory import create_app

        settings = Settings(
            app_id='test-app',
            app_secret='test-secret',
            base_url='http://demo.example',
            dev=True,
            allowed_shops=frozenset({'shop.example'}),
            db_path=tmp_path / 'test_integration.sqlite3',
            bind_host='127.0.0.1',
            port=8765,
            flask_secret_key='test-secret',
            flask_debug=False,
        )
        flask_app = create_app(settings)
        yield flask_app

    def test_health(self, app):
        """GET /health — 200."""
        with app.test_client() as c:
            resp = c.get('/health')
        assert resp.status_code == 200

    def test_install_received(self, app):
        """GET /install — сохранение кандидата, 200."""
        with app.test_client() as c:
            resp = c.get('/install?shop=shop.example&insales_id=42&token=abc')
        assert resp.status_code == 200

    def test_install_blocked_by_allowed_shops(self, app):
        """GET /install с неизвестным доменом — 400."""
        with app.test_client() as c:
            resp = c.get('/install?shop=evil.example&insales_id=99&token=abc')
        assert resp.status_code == 400

    def test_install_missing_params(self, app):
        """GET /install без параметров — 400."""
        with app.test_client() as c:
            resp = c.get('/install')
        assert resp.status_code == 400

    def test_login_without_install(self, app):
        """GET /login без установки — 404."""
        with app.test_client() as c:
            resp = c.get('/login?shop=nobody.example&insales_id=999')
        assert resp.status_code == 404

    def test_unauthenticated_dashboard(self, app):
        """GET / без сессии — 401."""
        with app.test_client() as c:
            resp = c.get('/')
        assert resp.status_code == 401

    def test_not_found(self, app):
        """GET /nonexistent — 404."""
        with app.test_client() as c:
            resp = c.get('/nonexistent')
        assert resp.status_code == 404
