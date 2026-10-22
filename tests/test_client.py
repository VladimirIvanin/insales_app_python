"""Тесты HTTP-транспорта и ресурсов InSales API."""
import json
from unittest.mock import MagicMock, patch

import pytest

from insales.client import InSalesClient
from insales.errors import ApiError, ResourceError, ResponseTooLargeError
from insales.resources.base import Resource
from insales.transport import (
    MAX_RESPONSE_BYTES,
    RetryStrategy,
    Transport,
    validate_shop,
)


class TestResourceBase:
    """Тесты базового класса Resource: URL-сборка и query string."""

    def test_object_path_default(self):
        """По умолчанию _object_path подставляет id в /collection/{id}.json."""
        class R(Resource):
            _collection_path = '/admin/products.json'

        r = R(MagicMock())
        assert r._object_path(42) == '/admin/products/42.json'
        assert r._object_path(1) == '/admin/products/1.json'

    def test_object_path_collection_without_dot_json(self):
        """Работает, если в _collection_path нет .json — добавляет .json."""
        class R(Resource):
            _collection_path = '/admin/products'

        r = R(MagicMock())
        assert r._object_path(5) == '/admin/products/5.json'

    def test_object_path_template_override(self):
        """_object_path_template даёт полный контроль над форматом."""
        class R(Resource):
            _collection_path = '/admin/some.json'
            _object_path_template = '/admin/some/{id}.xml'

        r = R(MagicMock())
        assert r._object_path(7) == '/admin/some/7.xml'

    def test_collection_property(self):
        """_collection возвращает _collection_path как есть."""
        class R(Resource):
            _collection_path = '/admin/orders.json'

        r = R(MagicMock())
        assert r._collection == '/admin/orders.json'

    def test_build_query_empty(self):
        """Без параметров — пустая строка."""
        r = Resource(MagicMock())
        assert r._build_query() == ''

    def test_build_query_none_skipped(self):
        """Параметры с None пропускаются."""
        r = Resource(MagicMock())
        assert r._build_query(page=1, status=None) == '?page=1'

    def test_build_query_simple(self):
        """Простые параметры кодируются корректно."""
        r = Resource(MagicMock())
        result = r._build_query(page=1, per_page=50)
        assert result in ('?page=1&per_page=50', '?per_page=50&page=1')

    def test_build_query_with_date(self):
        """Даты со спецсимволами кодируются через urlencode."""
        r = Resource(MagicMock())
        result = r._build_query(
            created_at_from='2024-01-01 12:00:00',
            status='paid',
        )
        assert 'created_at_from' in result
        assert '2024-01-01' in result
        assert '12%3A00%3A00' in result or '+' in result

    def test_build_query_with_list(self):
        """Списки кодируются как multiple params (doseq=True)."""
        r = Resource(MagicMock())
        result = r._build_query(status=['new', 'paid'])
        # urlencode with doseq=True даёт status=new&status=paid
        assert 'status=new' in result
        assert 'status=paid' in result


class TestValidateShop:
    def test_valid_shop(self):
        assert validate_shop('shop.example') == 'shop.example'
        assert validate_shop('SHOP.EXAMPLE') == 'shop.example'

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
        from http.client import HTTPConnection, HTTPSConnection

        loopback = Transport('127.0.0.1', 'app', 'pwd', dev=True)
        conn = loopback._connect()
        assert isinstance(conn, HTTPConnection)
        assert not isinstance(conn, HTTPSConnection)

        external = Transport('shop.example', 'app', 'pwd', dev=True)
        conn2 = external._connect()
        assert isinstance(conn2, HTTPSConnection)

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

    def _make_conn(self, status=200, data=None, usage='1/500'):
        conn = MagicMock()
        response = MagicMock()
        response.status = status
        response.read.return_value = json.dumps(
            data if data is not None else {'id': 1}
        ).encode()
        response.getheader.side_effect = lambda h, *a: {
            'API-Usage-Limit': usage,
            'Retry-After': None,
        }.get(h)
        response.getheaders.return_value = [('API-Usage-Limit', usage)]
        conn.getresponse.return_value = response
        return conn

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

    def test_account_get_url(self, client):
        """Account.get() обращается к /admin/account.json."""
        conn = self._make_conn()
        with patch.object(client._transport, '_connect', return_value=conn):
            account = client.account.get()
        assert account == {'id': 1}

    def test_orders_get_url(self, client):
        """Orders.get(123) обращается к /admin/orders/123.json."""
        conn = self._make_conn()
        with patch.object(client._transport, '_connect', return_value=conn):
            order = client.orders.get(123)
        assert order == {'id': 1}

    def test_orders_list_url(self, client):
        """Orders.list() обращается с query params."""
        conn = self._make_conn(data=[{'id': 1}])
        with patch.object(client._transport, '_connect', return_value=conn):
            orders = client.orders.list(page=1, per_page=50)
        assert orders == [{'id': 1}]

    def test_orders_list_not_a_list_raises(self, client):
        """Orders.list() кидает ResourceError, если ответ не список."""
        conn = self._make_conn(data={'error': 'not a list'})
        with patch.object(client._transport, '_connect', return_value=conn):
            with pytest.raises(ResourceError, match='list'):
                client.orders.list()

    def test_orders_get_not_a_dict_raises(self, client):
        """Orders.get() кидает ResourceError, если ответ не dict."""
        conn = self._make_conn(data=[1, 2, 3])
        with patch.object(client._transport, '_connect', return_value=conn):
            with pytest.raises(ResourceError, match='order'):
                client.orders.get(1)

    def test_orders_count_url(self, client):
        """Orders.count() обращается к /admin/orders/count.json."""
        conn = self._make_conn(data={'count': 42})
        with patch.object(client._transport, '_connect', return_value=conn):
            count = client.orders.count()
        assert count == 42

    def test_orders_count_missing_field_raises(self, client):
        """Orders.count() кидает ResourceError при отсутствии 'count'."""
        conn = self._make_conn(data={'total': 42})
        with patch.object(client._transport, '_connect', return_value=conn):
            with pytest.raises(ResourceError, match='count'):
                client.orders.count()

    def test_orders_update_url(self, client):
        """Orders.update(123) обращается к /admin/orders/123.json."""
        conn = self._make_conn(data={'id': 123, 'status': 'paid'})
        with patch.object(client._transport, '_connect', return_value=conn):
            order = client.orders.update(123, {'order': {'status': 'paid'}})
        assert order['status'] == 'paid'

    def test_products_get_url(self, client):
        """Products.get(456) обращается к /admin/products/456.json."""
        conn = self._make_conn(data={'id': 456})
        with patch.object(client._transport, '_connect', return_value=conn):
            product = client.products.get(456)
        assert product['id'] == 456

    def test_products_count_url(self, client):
        """Products.count() обращается к /admin/products/count.json."""
        conn = self._make_conn(data={'count': 10})
        with patch.object(client._transport, '_connect', return_value=conn):
            count = client.products.count()
        assert count == 10

    def test_webhooks_get_url(self, client):
        """Webhooks.get(789) обращается к /admin/webhooks/789.json."""
        conn = self._make_conn(data={'id': 789})
        with patch.object(client._transport, '_connect', return_value=conn):
            wh = client.webhooks.get(789)
        assert wh['id'] == 789

    def test_webhooks_delete_raises_on_error(self, client):
        """Webhooks.delete() пробрасывает ошибки API."""
        conn = self._make_conn(status=404, data={'error': 'not found'})
        with patch.object(client._transport, '_connect', return_value=conn):
            with pytest.raises(ApiError) as exc:
                client.webhooks.delete(999)
        assert exc.value.status == 404

    def test_webhooks_create_url(self, client):
        """Webhooks.create() POST на /admin/webhooks.json."""
        conn = self._make_conn(data={'id': 1, 'topic': 'orders/create'})
        with patch.object(client._transport, '_connect', return_value=conn):
            wh = client.webhooks.create('orders/create', 'http://hook.example')
        assert wh['id'] == 1


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
