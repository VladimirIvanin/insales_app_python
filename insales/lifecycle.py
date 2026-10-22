"""Жизненный цикл устанавливаемого приложения InSales.

Содержит логику обработки install callback (приём кандидата,
фоновая верификация через account API, активация), uninstall
и утилиты для login/autologin.
"""
from __future__ import annotations

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from insales.client import InSalesClient
from insales.crypto import (
    generate_nonce,
    generate_session_id,
    secure_compare,
    sign_token3,
    store_password,
)
from insales.errors import ApiError, TransportError
from insales.store import Store
from insales.transport import validate_shop

if TYPE_CHECKING:
    from app.integrations.insales.subscriptions import SubscriptionService

INSTALL_WORKERS = 2
MAX_CANDIDATES_PER_SHOP = 5
CANDIDATE_TTL = 600  # 10 минут — кандидаты дольше этого срока считаются устаревшими
CHALLENGE_TTL = 300  # 5 минут
SESSION_TTL = 3600  # 1 час


class LifecycleError(Exception):
    """Ошибка жизненного цикла приложения."""


class Lifecycle:
    """Жизненный цикл устанавливаемого приложения InSales.

    Создание (``__init__``) только сохраняет конфигурацию.
    Для запуска worker'ов и восстановления pending установок
    необходимо вызвать ``start()``.
    """

    def __init__(self, store: Store, app_id: str, app_secret: str,
                 dev: bool = False,
                 allowed_shops: set[str] | None = None,
                 subscription_service: SubscriptionService | None = None):
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.dev = dev
        self.allowed_shops = allowed_shops or set()
        # Запрещаем пустой allowlist — используется в store.py
        # (проверяется также в app/config.py)
        self._subscription_service = subscription_service
        self._workers: ThreadPoolExecutor | None = None

    # ---- Управление worker'ами ----

    def start(self) -> None:
        """Запустить pool worker'ов и восстановить pending установки."""
        if self._workers is not None:
            return  # уже запущен
        self._workers = ThreadPoolExecutor(max_workers=INSTALL_WORKERS)
        self._resume_pending_installs()

    def shutdown(self) -> None:
        """Остановить worker pool."""
        if self._workers is not None:
            self._workers.shutdown(wait=True)
            self._workers = None

    @property
    def running(self) -> bool:
        return self._workers is not None

    def _resume_pending_installs(self) -> None:
        """Восстановить незавершённые установки после перезапуска."""
        if self._workers is None:
            return
        for install in self.store.list_installs():
            self._workers.submit(self._verify_install_worker, install['candidate'])

        # Восстановить подписки для магазинов, где они отсутствуют
        self._resume_missing_subscriptions()

    def _resume_missing_subscriptions(self) -> None:
        """Проверить и восстановить webhook-подписки для активных магазинов."""
        if self._subscription_service is None:
            return
        for store_row in self.store.list_stores():
            store_id = store_row['id']
            store_data = self.store.get_store(store_id)
            if store_data is None:
                continue
            # Подписки будут созданы при первом обращении к ним
            # через проверку в register_defaults
            try:
                client = self._make_client(
                    store_data['shop'], store_data['password'],
                )
                self._subscription_service.register_defaults(
                    client, store_id, store_data['generation'],
                )
            except Exception:
                import logging
                log = logging.getLogger(__name__)
                log.exception(
                    'Failed to restore subscriptions for store %s',
                    store_id,
                )

    # ---- Install ----

    def process_install(self, shop: str, insales_id: str,
                        token: str) -> str | None:
        """Обработать install callback.

        1. Валидировать параметры
        2. Проверить ALLOWED_SHOPS (обязателен)
        3. Вычислить пароль магазина
        4. Сохранить кандидата с поколением установки
        5. Запланировать фоновую верификацию

        Возвращает candidate_id (для логирования/отладки).
        """
        shop = validate_shop(shop)
        if not insales_id.isdigit():
            raise ValueError(f'Invalid insales_id: {insales_id}')
        if not token:
            raise ValueError('Empty install token')
        if not self.allowed_shops:
            raise ValueError('ALLOWED_SHOPS is empty — installation rejected')
        if shop not in self.allowed_shops:
            raise ValueError(f'Shop {shop} is not in ALLOWED_SHOPS')

        # Throttling: очищаем устаревших кандидатов и проверяем лимит
        self.store.delete_stale_installs(CANDIDATE_TTL)
        if len(self.store.list_installs_by_shop(shop)) >= MAX_CANDIDATES_PER_SHOP:
            raise ValueError(f'Too many pending installs for shop {shop}')

        password = store_password(token, self.app_secret)
        candidate = secrets.token_urlsafe(32)

        # Атомарно: резервируем поколение и вставляем кандидата
        self.store.reserve_generation_and_add_install(
            candidate, insales_id, shop, password,
        )
        if self._workers is not None:
            self._workers.submit(self._verify_install_worker, candidate)
        return candidate

    def _make_client(self, shop: str, password: str) -> InSalesClient:
        """Создать клиент API для магазина. Выделено для тестов."""
        return InSalesClient(shop, self.app_id, password, dev=self.dev)

    def _create_default_subscriptions(self, client: InSalesClient,
                                       store_id: str,
                                       generation: int) -> None:
        """Создать стандартные webhook-подписки после активации."""
        if self._subscription_service is None:
            import logging
            log = logging.getLogger(__name__)
            log.warning(
                'SubscriptionService not configured — '
                'webhook subscriptions not created for store %s',
                store_id,
            )
            return
        try:
            self._subscription_service.register_defaults(
                client, store_id, generation,
            )
        except Exception:
            import logging
            log = logging.getLogger(__name__)
            log.exception(
                'Failed to create webhook subscriptions for store %s',
                store_id,
            )

    def _verify_install_worker(self, candidate: str,
                                 attempt: int = 0) -> None:
        """Фоновая верификация кандидата установки.

        При временных ошибках повторяет попытку с экспоненциальной
        задержкой (10 с, 30 с, 60 с). Если исчерпаны попытки или
        истёк CANDIDATE_TTL — кандидат удаляется.
        """
        install = self.store.get_install(candidate)
        if install is None:
            return

        client = self._make_client(install['shop'], install['password'])

        try:
            account = client.get_account()[0]
            if not isinstance(account, dict):
                self.store.delete_install(candidate)
                return
            if str(account.get('id')) != str(install['store_id']):
                self.store.delete_install(candidate)
                return

            ok = self.store.activate_install(candidate,
                                             account.get('title', 'Store'))
            if ok:
                # Активировано — создаём webhook-подписки
                self._create_default_subscriptions(
                    client, install['store_id'], install['generation'],
                )
            return

        except ApiError as exc:
            if exc.is_temporary:
                self._retry_or_discard(candidate, attempt)
                return
            self.store.delete_install(candidate)
            return
        except TransportError:
            self._retry_or_discard(candidate, attempt)
            return
        except (OSError, ValueError):
            self._retry_or_discard(candidate, attempt)
            return
        except Exception:
            self.store.delete_install(candidate)
            return

    def _retry_or_discard(self, candidate: str, attempt: int) -> None:
        """Повторить верификацию с задержкой или удалить кандидата."""
        delays = [10, 30, 60]
        if attempt >= len(delays):
            self.store.delete_install(candidate)
            return
        delay = delays[attempt]
        if CANDIDATE_TTL > 0:
            # Удаляем устаревшие кандидаты перед повтором
            self.store.delete_stale_installs(CANDIDATE_TTL)
        import time
        time.sleep(delay)
        self._verify_install_worker(candidate, attempt + 1)

    # ---- Uninstall ----

    def process_uninstall(self, shop: str, insales_id: str,
                          password: str) -> bool:
        """Обработать uninstall callback.

        InSales присылает в uninstall уже готовый пароль
        ``MD5(install_token + app_secret)`` — тот же, что был сохранён
        при install. Повторное хэширование не требуется.

        Использует атомарную операцию ``uninstall_store_or_pending_install``,
        которая в одной write-транзакции проверяет и active store,
        и pending install, исключая race condition с фоновой активацией.

        Возвращает True при успешном удалении, False если магазин не найден.
        """
        shop = validate_shop(shop)

        # Единая атомарная операция: active store ИЛИ pending install
        return self.store.uninstall_store_or_pending_install(
            insales_id, shop, password,
        )

    # ---- Login / Autologin ----

    def create_challenge(self, store_id: str) -> tuple[str, str, str]:
        """Создать challenge для login flow.

        Возвращает (cookie_sid, nonce, challenge_sid).
        """
        store_row = self.store.get_store(store_id)
        if store_row is None:
            raise LifecycleError('Store not installed')

        challenge_sid = generate_session_id()
        nonce = generate_nonce()

        self.store.add_challenge(challenge_sid, store_id, nonce, CHALLENGE_TTL)
        return challenge_sid, nonce, challenge_sid

    def verify_autologin(self, challenge_sid: str, cookie_sid: str,
                         params: dict) -> str | None:
        """Проверить autologin callback.

        Принимает challenge_sid из cookie, параметры из callback InSales.
        Возвращает новый session_id при успехе, None при ошибке.
        """
        if not challenge_sid or challenge_sid != cookie_sid:
            return None

        challenge = self.store.consume_challenge(challenge_sid)
        if challenge is None or challenge['expires'] < time.time():
            return None

        store_row = self.store.get_store(challenge['store_id'])
        if store_row is None:
            return None

        expected = sign_token3(
            nonce=challenge['nonce'],
            user_email=params.get('user_email', ''),
            user_name=params.get('user_name', ''),
            user_id=params.get('user_id', ''),
            email_confirmed=params.get('email_confirmed', 'false'),
            password=store_row['password'],
        )

        signature = params.get('token3', '')
        if not secure_compare(signature, expected):
            return None

        session_id = generate_session_id()
        # Привязываем сессию к текущему поколению магазина
        self.store.add_session(
            sid=session_id,
            store_id=store_row['id'],
            user_id=params.get('user_id', ''),
            ttl=SESSION_TTL,
            generation=store_row['generation'],
        )
        return session_id
