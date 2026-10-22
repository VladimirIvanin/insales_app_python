"""Тесты HTTP-транспорта и ресурсов InSales API."""
import json
from unittest.mock import MagicMock, patch

import pytest

from insales.client import InSalesClient
from insales.errors import ApiError, ResponseTooLargeError
from insales.transport import (
    MAX_RESPONSE_BYTES,
    RetryStrategy,
    Transport,
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


class TestTransport:
    @pytest.fixture
    def transport(self):
        return Transport(
            shop='shop.example',
            app_id='test-app',
            password='test-password',
            dev=True,
        )

    def _mock_response(self, status=200, body=None, usage='1/500'):
        conn = MagicMock()
        response = MagicMock()
        response.status = status
        if body is not None:
            response.read.return_value = body
        else:
            response.read.return_value = json.dumps(
                {'id': 42, 'title': 'Test'}).encode()
        response.getheader.side_effect = lambda h, *a: {
            'API-Usage-Limit': usage,
            'Retry-After': None,
        }.get(h)
        response.getheaders.return_value = [('API-Usage-Limit', usage)]
        conn.getresponse.return_value = response
        return conn

    def test_get_account_success(self, transport):
        conn = self._mock_response()
        with patch.object(transport, '_connect', return_value=conn):
            resp = transport.get('/admin/account.json')
        assert resp.data['id'] == 42
        assert resp.usage == '1/500'

    def test_api_error_status(self, transport):
        conn = self._mock_response(status=401, body=b'Unauthorized', usage=None)
        with patch.object(transport, '_connect', return_value=conn):
            with pytest.raises(ApiError) as exc:
                transport.get('/admin/account.json')
        assert exc.value.status == 401

    def test_response_too_large(self, transport):
        conn = self._mock_response(body=b'x' * (MAX_RESPONSE_BYTES + 1))
        with patch.object(transport, '_connect', return_value=conn):
            with pytest.raises(ResponseTooLargeError):
                transport.get('/admin/account.json')

    def test_dev_mode_http_only_for_loopback(self):
        loopback = Transport('127.0.0.1', 'app', 'pwd', dev=True)
        conn = loopback._connect()
        assert isinstance(conn, type(conn))  # HTTPConnection

        external = Transport('shop.example', 'app', 'pwd', dev=True)
        conn2 = external._connect()
        assert isinstance(conn2, type(conn2))  # HTTPSConnection

    def test_verify_identity_match(self, transport):
        conn = self._mock_response()
        with patch.object(transport, '_connect', return_value=conn):
            assert transport.verify_identity('42') is True

    def test_verify_identity_mismatch(self, transport):
        conn = self._mock_response()
        with patch.object(transport, '_connect', return_value=conn):
            assert transport.verify_identity('99') is False

    def test_verify_identity_api_error(self, transport):
        conn = MagicMock()
        conn.getresponse.side_effect = OSError('Connection failed')
        with patch.object(transport, '_connect', return_value=conn):
            assert transport.verify_identity('42') is False


class TestInSalesClient:
    @pytest.fixture
    def client(self):
        return InSalesClient(
            shop='shop.example',
            app_id='test-app',
            password='test-password',
            dev=True,
        )

    def test_client_has_resources(self, client):
        assert client.account is not None
        assert client.webhooks is not None
        assert client.orders is not None
        assert client.products is not None

    def test_client_legacy_get(self, client):
        conn = MagicMock()
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps([{'id': 1}]).encode()
        response.getheader.side_effect = lambda h, *a: {
            'API-Usage-Limit': '2/500',
            'Retry-After': None,
        }.get(h)
        response.getheaders.return_value = [('API-Usage-Limit', '2/500')]
        conn.getresponse.return_value = response

        with patch.object(client._transport, '_connect', return_value=conn):
            data, usage = client.get('/admin/orders.json')
        assert data == [{'id': 1}]
        assert usage == '2/500'


class TestRetryStrategy:
    def test_should_retry_temporary(self):
        strategy = RetryStrategy(max_retries=3)
        error = ApiError(429)
        assert strategy.should_retry(0, error)
        assert strategy.should_retry(2, error)
        assert not strategy.should_retry(3, error)

    def test_should_not_retry_permanent(self):
        strategy = RetryStrategy(max_retries=3)
        error = ApiError(401)
        assert not strategy.should_retry(0, error)

    def test_get_delay_respects_retry_after(self):
        strategy = RetryStrategy(max_retries=3)
        delay = strategy.get_delay(0, retry_after='5')
        assert delay == 5.0

    def test_get_delay_exponential(self):
        strategy = RetryStrategy(max_retries=3, base_delay=1.0)
        d0 = strategy.get_delay(0)
        d1 = strategy.get_delay(1)
        d2 = strategy.get_delay(2)
        # With 50% jitter, minimum is base_delay * 0.5 * (2^n)
        assert d0 >= 0.5
        assert d1 >= 1.0
        assert d2 >= 2.0
