"""Тесты криптографических формул InSales."""
import json
from pathlib import Path

from insales.crypto import (
    generate_nonce,
    generate_session_id,
    md5,
    secure_compare,
    sign_token,
    sign_token2,
    sign_token3,
    store_password,
)

VECTORS = json.loads(
    Path(__file__).with_name('protocol-vectors.json').read_text()
)


class TestMd5:
    def test_basic_md5(self):
        """Проверка базовой работы MD5."""
        result = md5('hello', 'world')
        assert result == 'fc5e038d38a57032085441e7fe7010b0'

    def test_md5_unicode(self):
        """MD5 с Unicode-строками."""
        result = md5('тест', 'проверка')
        assert isinstance(result, str)
        assert len(result) == 32

    def test_empty_parts(self):
        """MD5 с пустыми строками."""
        result = md5('', '')
        assert result == hashlib.md5(b'').hexdigest()  # noqa: F821
        # d41d8cd98f00b204e9800998ecf8427e


class TestStorePassword:
    def test_known_vector(self):
        """Проверка по известному вектору."""
        v = VECTORS['install']
        result = store_password(v['install_token'], v['app_secret'])
        assert result == v['expected_password']

    def test_different_token_different_password(self):
        """Разные токены дают разные пароли."""
        p1 = store_password('token1', 'secret')
        p2 = store_password('token2', 'secret')
        assert p1 != p2


class TestSignatures:
    def test_token_vector(self):
        """token: MD5(nonce + password)."""
        v = VECTORS['autologin']
        result = sign_token(v['nonce'], v['password'])
        assert result == v['expected_token']

    def test_token2_vector(self):
        """token2: MD5(nonce + email + name + user_id + password)."""
        v = VECTORS['autologin']
        result = sign_token2(
            v['nonce'], v['user_email'], v['user_name'],
            v['user_id'], v['password'],
        )
        assert result == v['expected_token2']

    def test_token3_vector(self):
        """token3: основной вектор проверки."""
        v = VECTORS['autologin']
        result = sign_token3(
            v['nonce'], v['user_email'], v['user_name'],
            v['user_id'], v['email_confirmed'], v['password'],
        )
        assert result == v['expected_token3']

    def test_token3_email_confirmed_false(self):
        """email_confirmed=false меняет подпись."""
        v = VECTORS['autologin']
        with_false = sign_token3(
            v['nonce'], v['user_email'], v['user_name'],
            v['user_id'], 'false', v['password'],
        )
        with_true = sign_token3(
            v['nonce'], v['user_email'], v['user_name'],
            v['user_id'], 'true', v['password'],
        )
        assert with_false != with_true


class TestSecureCompare:
    def test_equal(self):
        """Одинаковые хеши — True."""
        assert secure_compare(
            '3f35a77d97b71a4bf503747602ad2f1f',
            '3f35a77d97b71a4bf503747602ad2f1f',
        )

    def test_different(self):
        """Разные хеши — False."""
        assert not secure_compare(
            '3f35a77d97b71a4bf503747602ad2f1f',
            '00000000000000000000000000000000',
        )

    def test_invalid_format(self):
        """Невалидный hex — False без исключения."""
        assert not secure_compare('xyz', 'abc')
        assert not secure_compare('', '')
        assert not secure_compare(
            '3f35a77d97b71a4bf503747602ad2f1f',
            'short',
        )


class TestGeneration:
    def test_nonce_is_unique(self):
        """Сгенерированные nonce различны."""
        n1 = generate_nonce()
        n2 = generate_nonce()
        assert n1 != n2
        assert len(n1) > 16

    def test_session_id_is_unique(self):
        """Сгенерированные session_id различны."""
        s1 = generate_session_id()
        s2 = generate_session_id()
        assert s1 != s2
        assert len(s1) > 16


import hashlib  # noqa: E402, S324 — для test_md5_empty_parts
