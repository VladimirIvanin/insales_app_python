"""Тесты webhook-инфраструктуры: подписки, inbox, receiver, dispatcher, worker.

Проверяют:
- WebhookSubscriptions: create, get, get_by_secret, update_insales_id, list
- WebhookInbox: push, acquire, release, mark_success, mark_failed, cancel_by_store
- WebhookReceiver: приём с проверкой токена, JSON, размера
- WebhookDispatcher: регистрация и маршрутизация handler'ов
- WebhookWorker: базовая обработка и retry
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.integrations.insales.handlers.orders import (
    handle_order_create,
    handle_order_delete,
    handle_order_update,
)
from app.integrations.insales.subscriptions import SubscriptionService
from app.integrations.insales.webhook_dispatcher import WebhookDispatcher
from app.integrations.insales.webhook_receiver import WebhookReceiver
from app.repositories.webhook_inbox import WebhookInbox
from app.repositories.webhook_subscriptions import WebhookSubscriptions
from app.workers.webhooks import WebhookWorker
from insales.resources.webhooks import Webhooks
from insales.store import Store


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / 'test.sqlite3'


@pytest.fixture
def store(tmp_db):
    from app.db.connection import upgrade
    upgrade(tmp_db)
    s = Store(tmp_db)
    return s


@pytest.fixture
def preinstalled_store(store):
    store.add_store('42', 'shop.example', 'password-abc', title='Shop')
    store.add_store('43', 'another.shop', 'password-xyz', title='Another')
    return store


@pytest.fixture
def subs(preinstalled_store):
    return WebhookSubscriptions(preinstalled_store)


@pytest.fixture
def inbox(preinstalled_store):
    return WebhookInbox(preinstalled_store)


# =============================================================================
# WebhookSubscriptions
# =============================================================================


class TestWebhookSubscriptions:
    def test_create_and_get(self, subs):
        sub = subs.create('42', 1, 'orders/update',
                           base_url='http://example.com')
        assert sub['store_id'] == '42'
        assert sub['topic'] == 'orders/update'
        assert sub['state'] == 'active'
        assert sub['generation'] == 1
        assert 'token=' in sub['address']
        assert sub['callback_secret']

        fetched = subs.get(sub['id'])
        assert fetched is not None
        assert fetched['id'] == sub['id']

    def test_get_by_secret(self, subs):
        sub = subs.create('42', 1, 'orders/create',
                           base_url='http://example.com')
        fetched = subs.get_by_secret(sub['callback_secret'])
        assert fetched is not None
        assert fetched['id'] == sub['id']

    def test_get_by_secret_unknown(self, subs):
        assert subs.get_by_secret('nonexistent') is None

    def test_get_by_store_and_topic(self, subs):
        subs.create('42', 1, 'orders/update',
                     base_url='http://example.com')
        found = subs.get_by_store_and_topic('42', 'orders/update')
        assert found is not None
        assert found['topic'] == 'orders/update'

        not_found = subs.get_by_store_and_topic('42', 'orders/create')
        assert not_found is None

    def test_create_updates_existing(self, subs):
        sub1 = subs.create('42', 1, 'orders/update',
                            base_url='http://example.com')
        old_secret = sub1['callback_secret']
        sub1['id']

        subs.create('42', 2, 'orders/update',
                            base_url='http://example.com')
        # ON CONFLICT заменяет, но lastrowid может быть None
        # при upsert; проверяем что это та же самая тема
        fetched = subs.get_by_store_and_topic('42', 'orders/update')
        assert fetched['callback_secret'] != old_secret
        assert fetched['generation'] == 2

    def test_update_insales_id(self, subs):
        sub = subs.create('42', 1, 'orders/update',
                           base_url='http://example.com')
        ok = subs.update_insales_id(sub['id'], 12345)
        assert ok is True

        fetched = subs.get(sub['id'])
        assert fetched['insales_id'] == 12345

    def test_list_by_store(self, subs):
        subs.create('42', 1, 'orders/create', base_url='http://example.com')
        subs.create('42', 1, 'orders/update', base_url='http://example.com')
        subs.create('43', 1, 'clients/create', base_url='http://example.com')

        list_42 = subs.list_by_store('42')
        assert len(list_42) == 2

        list_43 = subs.list_by_store('43')
        assert len(list_43) == 1

    def test_mark_inactive(self, subs):
        sub = subs.create('42', 1, 'orders/update',
                           base_url='http://example.com')
        ok = subs.mark_inactive(sub['id'])
        assert ok is True

        fetched = subs.get(sub['id'])
        assert fetched['state'] == 'inactive'

    def test_delete_by_store(self, subs):
        subs.create('42', 1, 'orders/create', base_url='http://example.com')
        subs.create('42', 1, 'orders/update', base_url='http://example.com')
        count = subs.delete_by_store('42')
        assert count == 2

        assert subs.list_by_store('42') == []


# =============================================================================
# WebhookInbox
# =============================================================================


class TestWebhookInbox:
    def test_push_and_get(self, inbox):
        event_id = inbox.push(
            store_id='42', generation=1, topic='orders/update',
            payload={'order': {'id': 100}},
        )
        assert event_id > 0

        event = inbox.get(event_id)
        assert event is not None
        assert event['store_id'] == '42'
        assert event['topic'] == 'orders/update'
        assert event['state'] == 'pending'

        payload = json.loads(event['payload'])
        assert payload['order']['id'] == 100

    def test_list_pending_empty(self, inbox):
        assert inbox.list_pending(limit=10) == []

    def test_acquire_and_release(self, inbox):
        event_id = inbox.push('42', 1, 'orders/create',
                               {'order': {'id': 1}})

        ok = inbox.acquire(event_id, 'worker-1')
        assert ok is True

        event = inbox.get(event_id)
        assert event['locked_by'] == 'worker-1'
        assert event['locked_at'] is not None

        ok = inbox.release(event_id)
        assert ok is True

        event = inbox.get(event_id)
        assert event['locked_by'] is None

    def test_acquire_already_locked(self, inbox):
        event_id = inbox.push('42', 1, 'orders/create',
                               {'order': {'id': 1}})
        inbox.acquire(event_id, 'worker-1')
        ok = inbox.acquire(event_id, 'worker-2')
        assert ok is False  # locked by worker-1

    def test_acquire_expired_lock(self, inbox):
        event_id = inbox.push('42', 1, 'orders/create',
                               {'order': {'id': 1}})
        inbox.acquire(event_id, 'worker-1')

        # manually set lock to expired
        with inbox._store._connect() as conn:
            conn.execute(
                'UPDATE webhook_inbox SET locked_at=0 WHERE id=?',
                (event_id,),
            )

        ok = inbox.acquire(event_id, 'worker-2')
        assert ok is True

    def test_mark_success(self, inbox):
        event_id = inbox.push('42', 1, 'orders/update',
                               {'order': {'id': 1}})
        inbox.mark_success(event_id)
        event = inbox.get(event_id)
        assert event['state'] == 'processed'

    def test_mark_failed_with_retry(self, inbox):
        event_id = inbox.push('42', 1, 'orders/update',
                               {'order': {'id': 1}})
        ok = inbox.mark_failed(event_id, 'Temporary error')
        assert ok is True

        event = inbox.get(event_id)
        assert event['state'] == 'pending'  # будет повтор
        assert event['attempts'] == 1
        assert event['next_attempt_at'] is not None

    def test_mark_failed_permanent(self, inbox):
        event_id = inbox.push('42', 1, 'orders/update',
                               {'order': {'id': 1}})
        ok = inbox.mark_failed(event_id, 'Permanent error', permanent=True)
        assert ok is True

        event = inbox.get(event_id)
        assert event['state'] == 'failed'

    def test_cancel_by_store(self, inbox):
        inbox.push('42', 1, 'orders/create', {'order': {'id': 1}})
        inbox.push('42', 1, 'orders/update', {'order': {'id': 2}})
        inbox.push('43', 1, 'orders/update', {'order': {'id': 3}})

        cancelled = inbox.cancel_by_store('42')
        assert cancelled == 2

        assert inbox.count_pending('42') == 0
        assert inbox.count_pending('43') == 1


# =============================================================================
# WebhookReceiver
# =============================================================================


class TestWebhookReceiver:
    @pytest.fixture
    def receiver(self, subs, inbox):
        return WebhookReceiver(subs, inbox)

    def test_receive_unknown_token(self, receiver):
        result = receiver.receive(
            token='invalid-secret',
            body=json.dumps({'order': {'id': 1}}).encode(),
        )
        assert result.accepted is False
        assert result.status == 404

    def test_receive_empty_body(self, receiver, subs):
        subs.create('42', 1, 'orders/update',
                     base_url='http://example.com')
        sub = subs.get_by_store_and_topic('42', 'orders/update')
        result = receiver.receive(
            token=sub['callback_secret'],
            body=b'',
            headers={'Content-Type': 'application/json'},
        )
        assert result.accepted is False
        assert result.status == 400

    def test_receive_invalid_json(self, receiver, subs):
        subs.create('42', 1, 'orders/update',
                     base_url='http://example.com')
        sub = subs.get_by_store_and_topic('42', 'orders/update')
        result = receiver.receive(
            token=sub['callback_secret'],
            body=b'not json',
            headers={'Content-Type': 'application/json'},
        )
        assert result.accepted is False
        assert result.status == 400

    def test_receive_success_json(self, receiver, subs, inbox):
        subs.create('42', 1, 'orders/update',
                     base_url='http://example.com')
        sub = subs.get_by_store_and_topic('42', 'orders/update')

        result = receiver.receive(
            token=sub['callback_secret'],
            body=json.dumps({
                'id': 100,
                'financial_status': 'paid',
            }).encode(),
            headers={'Content-Type': 'application/json'},
        )
        assert result.accepted is True
        assert result.status == 200
        assert result.topic == 'orders/update'
        assert result.store_id == '42'
        assert result.inbox_id is not None

        event = inbox.get(result.inbox_id)
        assert event is not None
        assert event['store_id'] == '42'

    def test_receive_payload_too_large(self, receiver, subs):
        subs.create('42', 1, 'orders/update',
                     base_url='http://example.com')
        sub = subs.get_by_store_and_topic('42', 'orders/update')

        result = receiver.receive(
            token=sub['callback_secret'],
            body=b'x' * 2_000_000,
            headers={'Content-Type': 'application/json'},
        )
        assert result.accepted is False
        assert result.status == 413


# =============================================================================
# WebhookDispatcher
# =============================================================================


class TestWebhookDispatcher:
    @pytest.fixture
    def dispatcher(self):
        d = WebhookDispatcher()
        d.register('orders/create', handle_order_create)
        d.register('orders/update', handle_order_update)
        d.register('orders/delete', handle_order_delete)
        return d

    def test_dispatch_known_topic(self, dispatcher):
        result = dispatcher.dispatch(
            store_id='42', generation=1,
            topic='orders/update', payload={'id': 100},
        )
        assert result is True

    def test_dispatch_unknown_topic(self, dispatcher):
        result = dispatcher.dispatch(
            store_id='42', generation=1,
            topic='products/create', payload={'id': 100},
        )
        assert result is False

    def test_has_handler(self, dispatcher):
        assert dispatcher.has_handler('orders/update')
        assert not dispatcher.has_handler('unknown/topic')


# =============================================================================
# WebhookWorker
# =============================================================================


class TestWebhookWorker:
    @pytest.fixture
    def inbox_and_dispatcher(self, preinstalled_store):
        inbox = WebhookInbox(preinstalled_store)
        dispatcher = WebhookDispatcher()
        dispatcher.register('orders/create', handle_order_create)
        dispatcher.register('orders/update', handle_order_update)
        return inbox, dispatcher

    def test_process_event(self, inbox_and_dispatcher):
        inbox, dispatcher = inbox_and_dispatcher
        event_id = inbox.push('42', 1, 'orders/update',
                               {'order': {'id': 100}})

        worker = WebhookWorker(inbox, dispatcher,
                                poll_interval=0.1, batch_size=10)
        worker._process_batch()

        event = inbox.get(event_id)
        assert event['state'] == 'processed'

    def test_worker_start_stop(self, inbox_and_dispatcher):
        inbox, dispatcher = inbox_and_dispatcher
        worker = WebhookWorker(inbox, dispatcher,
                                poll_interval=0.1, batch_size=10)
        assert not worker.running

        worker.start()
        assert worker.running

        worker.stop(timeout=2)
        assert not worker.running

    def test_worker_processes_pending(self, inbox_and_dispatcher):
        inbox, dispatcher = inbox_and_dispatcher
        event_id = inbox.push('42', 1, 'orders/create',
                               {'order': {'id': 200}})

        worker = WebhookWorker(inbox, dispatcher,
                                poll_interval=0.05, batch_size=10)
        worker.start()
        time.sleep(0.3)
        worker.stop(timeout=2)

        event = inbox.get(event_id)
        assert event['state'] == 'processed'

    def test_worker_marks_failed_for_no_handler(self, inbox_and_dispatcher):
        inbox, dispatcher = inbox_and_dispatcher
        event_id = inbox.push('42', 1, 'unknown/topic',
                               {'data': 'test'})

        worker = WebhookWorker(inbox, dispatcher,
                                poll_interval=0.05, batch_size=10)
        worker._process_batch()

        event = inbox.get(event_id)
        assert event['state'] == 'failed'

    def test_worker_acquire_only_once(self, inbox_and_dispatcher):
        inbox, dispatcher = inbox_and_dispatcher
        event_id = inbox.push('42', 1, 'orders/update',
                               {'order': {'id': 300}})

        inbox.acquire(event_id, 'other-worker')

        worker = WebhookWorker(inbox, dispatcher,
                                poll_interval=0.1, batch_size=10)
        worker._process_batch()

        event = inbox.get(event_id)
        assert event['state'] == 'pending'  # still pending, not acquired


# =============================================================================
# SubscriptionService
# =============================================================================


class TestSubscriptionService:
    @pytest.fixture
    def service(self, subs):
        return SubscriptionService(subs, base_url='http://example.com')

    @pytest.fixture
    def mock_webhooks(self):
        return MagicMock(spec=Webhooks)

    def test_register_defaults(self, service, mock_webhooks, subs):
        """register_defaults использует client.webhooks."""
        mock_client = MagicMock()
        mock_client.webhooks = mock_webhooks
        mock_webhooks.create.return_value = {'id': 100}

        results = service.register_defaults(
            client=mock_client,
            store_id='42',
            generation=1,
        )

        assert len(results) == len(service.DEFAULT_TOPICS)
        assert mock_webhooks.create.call_count == len(service.DEFAULT_TOPICS)

        for sub in results:
            assert sub['store_id'] == '42'
            assert sub['state'] == 'active'

    def test_register_defaults_with_client(self, service, subs):
        """Полноценный тест с InSalesClient-like mock."""
        mock_client = MagicMock()
        mock_webhooks = MagicMock(spec=Webhooks)
        mock_webhooks.create.return_value = {'id': 100}
        mock_client.webhooks = mock_webhooks

        results = service.register_defaults(
            client=mock_client,
            store_id='42',
            generation=1,
        )

        assert len(results) == len(service.DEFAULT_TOPICS)
        assert mock_webhooks.create.call_count == len(service.DEFAULT_TOPICS)

        for sub in results:
            assert sub['store_id'] == '42'
            assert sub['state'] == 'active'

    def test_register_topic_creates_remote(self, service, subs):
        mock_client = MagicMock()
        mock_webhooks = MagicMock(spec=Webhooks)
        mock_webhooks.create.return_value = {'id': 200, 'topic': 'orders/create'}
        mock_client.webhooks = mock_webhooks

        sub = service.register_topic(
            client=mock_client,
            store_id='42',
            generation=1,
            topic='orders/create',
        )
        assert sub is not None
        assert sub['topic'] == 'orders/create'

        fetched = subs.get(sub['id'])
        assert fetched['insales_id'] == 200

    def test_register_topic_no_remote_reconciles(self, service, subs):
        mock_client = MagicMock()
        mock_webhooks = MagicMock(spec=Webhooks)
        mock_webhooks.create.return_value = None
        mock_webhooks.list.return_value = [
            {'id': 300, 'topic': 'orders/update'},
        ]
        mock_client.webhooks = mock_webhooks

        sub = service.register_topic(
            client=mock_client,
            store_id='42',
            generation=1,
            topic='orders/update',
        )
        assert sub is not None
        fetched = subs.get(sub['id'])
        assert fetched['insales_id'] == 300

    def test_unregister_all(self, service, subs):
        mock_client = MagicMock()
        mock_webhooks = MagicMock(spec=Webhooks)
        mock_webhooks.create.return_value = {'id': 400}
        mock_client.webhooks = mock_webhooks

        service.register_defaults(
            client=mock_client,
            store_id='42',
            generation=1,
        )

        mock_webhooks2 = MagicMock(spec=Webhooks)
        mock_webhooks2.delete.return_value = True
        mock_client2 = MagicMock()
        mock_client2.webhooks = mock_webhooks2

        service.unregister_all(mock_client2, '42')
        remaining = subs.list_by_store('42')
        assert len(remaining) == 0
