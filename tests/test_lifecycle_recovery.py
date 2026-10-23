from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from app.config import Settings
from app.factory import create_app
from insales.crypto import sign_token3, store_password
from insales.errors import ApiError, ResourceError
from insales.lifecycle import LifecycleError


def test_full_queue_admits_genuine_after_rejecting_forged_ids(lifecycle, store):
    for n in range(5):
        lifecycle.process_install('shop.example', str(900 + n), 'forged-' + str(n))
    api = Mock()
    api.get_account.side_effect = ApiError(401)
    with patch.object(lifecycle, '_make_client', return_value=api):
        actual = lifecycle.process_install('shop.example', '42', 'genuine')
    assert store.get_install(actual)['store_id'] == '42'
    assert len(store.pending_installs('shop.example')) == 5
    api.get_account.assert_called_once()


def test_pending_genuine_is_not_evicted_when_spam_arrives(lifecycle, store):
    actual = lifecycle.process_install('shop.example', '42', 'genuine')
    for n in range(4):
        lifecycle.process_install('shop.example', str(900 + n), 'forged-' + str(n))
    api = Mock()
    api.get_account.side_effect = [({'id': 42}, None), ApiError(401)]
    with patch.object(lifecycle, '_make_client', return_value=api):
        lifecycle.process_install('shop.example', '999', 'more-spam')
    assert store.get_install(actual) is not None
    assert len(store.pending_installs('shop.example')) == 5


@pytest.mark.parametrize('error', [ApiError(429, retry_after='60'), ResourceError('Invalid JSON')])
def test_transient_error_survives_retry_and_enforces_backoff(lifecycle, store, error):
    actual = lifecycle.process_install('shop.example', '42', 'genuine')
    api = Mock()
    api.get_account.side_effect = [error, ({'id': 42}, None)]
    with patch.object(lifecycle, '_make_client', return_value=api):
        for _ in range(2):
            with pytest.raises(LifecycleError, match='Temporary API error'):
                lifecycle.verify_pending_install('42', 'shop.example')
        assert api.get_account.call_count == 1
        assert store.get_install(actual) is not None
        with store._connect() as conn:
            conn.execute('UPDATE verification_budgets SET next_attempt_at=0')
        assert lifecycle.verify_pending_install('42', 'shop.example') == '42'
    assert api.get_account.call_count == 2
    assert store.get_store('42') is not None


def test_verified_domain_reuse_revokes_previous_owner(lifecycle, store):
    store.add_store('7', 'shop.example', store_password('old', lifecycle.app_secret), generation=1)
    store.add_session('old-session', '7', '1', generation=1)
    actual = lifecycle.process_install('shop.example', '42', 'genuine')
    api = Mock()
    api.get_account.return_value = ({'id': 42}, None)
    with patch.object(lifecycle, '_make_client', return_value=api):
        assert lifecycle.verify_pending_install('42', 'shop.example') == '42'
    assert store.get_store('7') is None
    assert store.get_session('old-session') is None
    assert store.get_store('42')['shop'] == 'shop.example'
    assert store.get_install(actual) is None


def test_generation_survives_deleting_highest_candidate(lifecycle, store):
    first = lifecycle.process_install('shop.example', '42', 'first')
    previous = store.get_install(first)['generation']
    store.delete_install(first)
    with patch('insales.store.time.time', return_value=1):
        later = lifecycle.process_install('shop.example', '42', 'second')
    assert store.get_install(later)['generation'] > previous


def test_domain_rename_and_overlapping_browser_flows(tmp_path):
    app = create_app(Settings(app_id='demo', app_secret='secret', base_url='http://demo.example',
        dev=True, allowed_shops=frozenset({'old.example', 'new.example', 'other.example'}),
        db_path=tmp_path/'app.sqlite', bind_host='127.0.0.1', port=8765,
        flask_secret_key='synthetic-key', flask_debug=False))
    store, lc = app.extensions['store'], app.extensions['lifecycle']
    password = store_password('genuine', 'secret')
    store.add_store('42', 'old.example', password, generation=1)
    store.add_store('43', 'other.example', password, generation=1)
    api = Mock()
    api.get_account.return_value = ({'id': 42}, None)
    client = app.test_client()
    with patch.object(lc, '_make_client', return_value=api):
        response = client.get(
            '/install',
            query_string={'shop': 'new.example', 'insales_id': '42', 'token': 'genuine'},
        )
        assert response.status_code == 200
        a = client.get('/login', query_string={'shop':'new.example','insales_id':'42'})
    assert a.status_code == 302
    b = client.get('/login', query_string={'shop':'other.example','insales_id':'43'})
    assert b.status_code == 302
    for response, store_id in [(a, '42'), (b, '43')]:
        query = parse_qs(urlsplit(response.location).query)
        nonce = query['token'][0]
        callback = query['login'][0]
        params = {
            'user_email': 'qa@example.invalid', 'user_name': 'QA',
            'user_id': '1', 'email_confirmed': 'true',
        }
        params['token3'] = sign_token3(nonce=nonce, password=password, **params)
        callback_url = urlsplit(callback)
        completed = client.get(
            callback_url.path + '?' + callback_url.query + '&' + urlencode(params)
        )
        assert completed.status_code == 302
        assert parse_qs(urlsplit(completed.location).query)['store_id'] == [store_id]
    assert client.get('/probe?store_id=42').status_code == 409
    assert store.get_store('42')['shop'] == 'new.example'
    unauthenticated = app.test_client().get('/')
    assert unauthenticated.status_code == 401
    assert 'Сессия недействительна' in unauthenticated.get_data(as_text=True)
    page = client.get('/?store_id=43')
    assert page.status_code == 200
    import re
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    assert client.post('/logout?store_id=43').status_code == 403
    assert client.get('/?store_id=43').status_code == 200
    assert client.post('/logout?store_id=43', data={'csrf_token':csrf}).status_code == 200
    assert client.get('/').status_code == 401


def test_legacy_cleanup_preserves_unexpired_credentials(store):
    for n in range(6):
        store.reserve_generation_and_add_install(
            f'candidate-{n}', str(100 + n), 'shop.example', f'password-{n}',
            max_candidates=6,
        )
    assert store.cleanup_stale_candidates('shop.example', max_candidates=5) == 0
    assert store.get_install('candidate-0') is not None
    assert len(store.pending_installs('shop.example')) == 6
