"""Тесты HTTP-клиента InSales API."""
import http.client
import json
from unittest.mock import MagicMock, patch

import pytest

from insales.client import (
    MAX_RESPONSE_BYTES,
    ApiError,
    InSalesClient,
    validate_shop,
)


class TestValidateShop:
    def test_valid_shop(self):
        assert validate_shop('shop.example') == 'shop.example'
        assert validate_shop('ABC.INSALES.IO') == 'abc.insales.io'

    def test_invalid_shop(self):
        with pytest.raises(ValueError):
            validate_shop('-bad.example')
        with pytest.raises(ValueError):
            validate_shop('')


class TestInSalesClient:
    @pytest.fixture
    def client(self):
        return InSalesClient(
            shop='shop.example',
            app_id='test-app',
            password='test-password',
            dev=True,
        )

    @pytest.fixture
    def client_loopback(self):
        return InSalesClient(
            shop='127.0.0.1',
            app_id='test-app',
            password='test-password',
            dev=True,
        )

    def _mock_conn(self, status=200, body=None, usage='1/500'):
        """Создать мок для HTTPConnection."""
        conn = MagicMock()
        response = MagicMock()
        response.status = status
        if body is not None:
            response.read.return_value = body
        else:
            response.read.return_value = json.dumps({'id': 42, 'title': 'Test'}).encode()
        response.getheader.return_value = usage
        conn.getresponse.return_value = response
        return conn

    def test_get_account_success(self, client):
        conn = self._mock_conn()
        with patch.object(client, '_connect', return_value=conn):
            account, usage = client.get_account()
        assert account['id'] == 42
        assert usage == '1/500'

    def test_api_error_status(self, client):
        conn = self._mock_conn(status=401, body=b'Unauthorized', usage=None)
        with patch.object(client, '_connect', return_value=conn):
            with pytest.raises(ApiError) as exc:
                client.get_account()
        assert exc.value.status == 401

    def test_response_too_large(self, client):
        conn = self._mock_conn(body=b'x' * (MAX_RESPONSE_BYTES + 1))
        with patch.object(client, '_connect', return_value=conn):
            with pytest.raises(ValueError, match='too large'):
                client.get_account()

    def test_verify_identity_match(self, client):
        conn = self._mock_conn()
        with patch.object(client, '_connect', return_value=conn):
            assert client.verify_identity('42') is True

    def test_verify_identity_mismatch(self, client):
        conn = self._mock_conn()
        with patch.object(client, '_connect', return_value=conn):
            assert client.verify_identity('99') is False

    def test_verify_identity_api_error(self, client):
        conn = MagicMock()
        conn.getresponse.side_effect = OSError('Connection failed')
        with patch.object(client, '_connect', return_value=conn):
            assert client.verify_identity('42') is False

    def test_dev_mode_http_only_for_loopback(self):
        """dev=True: HTTP только для loopback, для других хостов — HTTPS."""
        loopback_client = InSalesClient('127.0.0.1', 'app', 'pwd', dev=True)
        conn = loopback_client._connect()
        assert isinstance(conn, http.client.HTTPConnection)

        external_client = InSalesClient(
            'shop.example', 'app', 'pwd', dev=True,
        )
        conn2 = external_client._connect()
        assert isinstance(conn2, http.client.HTTPSConnection)
