"""Жизненный цикл устанавливаемого приложения InSales.

Содержит логику обработки install callback (приём кандидата),
проверку подключения при первом входе, uninstall
и утилиты для login/autologin.
"""
from __future__ import annotations

import secrets
import time

from insales.client import InSalesClient
from insales.crypto import (
    generate_nonce,
    generate_session_id,
    secure_compare,
    sign_token3,
    store_password,
)
from insales.errors import ApiError, ResourceError, TransportError
from insales.store import PendingQuotaError, Store
from insales.transport import validate_shop

MAX_CANDIDATES_PER_SHOP = 5
CANDIDATE_TTL = 86400 * 7  # 7 дней
CHALLENGE_TTL = 300  # 5 минут
SESSION_TTL = 3600  # 1 час


class LifecycleError(Exception):
    """Ошибка жизненного цикла приложения."""


class Lifecycle:
    """Жизненный цикл устанавливаемого приложения InSales.

    Не запускает фоновых потоков и worker'ов.
    Проверка подключения выполняется синхронно при входе.
    """

    def __init__(self, store: Store, app_id: str, app_secret: str,
                 dev: bool = False,
                 allowed_shops: set[str] | None = None):
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.dev = dev
        self.allowed_shops = allowed_shops or set()

    # ---- Install ----

    def process_install(self, shop: str, insales_id: str,
                        token: str) -> str | None:
        """Обработать install callback.

        1. Валидировать параметры
        2. Проверить ALLOWED_SHOPS (обязателен)
        3. Вычислить пароль магазина
        4. Сохранить кандидата с поколением установки

        При заполнении очереди ограниченно проверяет старых кандидатов вне транзакции.

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

        password = store_password(token, self.app_secret)
        candidate = secrets.token_urlsafe(32)

        active = self.store.get_store(insales_id)
        if active and active['shop'] == shop and secure_compare(active['password'], password):
            return None
        try:
            self.store.reserve_generation_and_add_install(
                candidate, insales_id, shop, password
            )
        except PendingQuotaError:
            deadline = time.monotonic() + 2
            for pending in self.store.pending_installs(shop):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                status, _ = self._check_candidate(pending, remaining)
                if status == 'invalid':
                    self.store.delete_install(pending['candidate'])
                    self.store.reserve_generation_and_add_install(
                candidate, insales_id, shop, password
            )
                    break
                if status == 'unavailable':
                    break
            else:
                raise LifecycleError('Pending installation limit reached')
            if self.store.get_install(candidate) is None:
                raise LifecycleError('Pending installation limit reached') from None
        return next((row['candidate'] for row in self.store.pending_installs(shop)
                     if row['store_id'] == insales_id and row['password'] == password), candidate)

    def _make_client(self, shop: str, password: str) -> InSalesClient:
        """Создать клиент API для магазина. Выделено для тестов."""
        return InSalesClient(shop, self.app_id, password, dev=self.dev)

    # ---- Проверка кандидата при входе ----

    def verify_pending_install(self, store_id: str, shop: str) -> str | None:
        """Проверить неподтверждённый кандидат установки при входе.

        Вызывается из /login. Работает как при отсутствии активного магазина,
        так и при его наличии: если есть более новый кандидат — выполняет
        API-запрос и при успехе атомарно заменяет активное подключение.

        Алгоритм:
        1. Прочитать самый новый кандидат для магазина.
        2. Выполнить один API-запрос account.json с его credentials.
        3. Проверить совпадение account.id с ожидаемым store_id.
        4. Атомарно активировать кандидата (защита от гонок с uninstall).

        Returns:
            store_id при успешной активации, None если кандидатов нет.
            При временной ошибке API кандидат сохраняется для повтора.

        Raises:
            LifecycleError: при достоверно неверных credentials
                            (кандидат удаляется).
        """
        install = self.store.get_latest_pending_install(store_id, shop)
        if install is None:
            return None

        status, account = self._check_candidate(install)
        if status != 'valid':
            if status == 'invalid':
                self.store.delete_install(install['candidate'])
            active = self.store.get_store(store_id)
            if active is not None and active['shop'] == shop:
                return active['id']
            if status == 'invalid':
                raise LifecycleError('API rejected credentials or Store ID mismatch')
            raise LifecycleError('Temporary API error verifying install; retry later')

        ok = self.store.activate_install(
            install['candidate'], account.get('title', 'Store'),
        )
        if not ok:
            # Кандидат был удалён/отменён во время запроса — не ошибка
            return None

        return install['store_id']

    def _check_candidate(self, install: dict, timeout: float = 5) -> tuple[str, dict | None]:
        permit = self.store.claim_verification(install['shop'])
        if permit is None:
            return 'unavailable', None
        delay = 0.0
        try:
            client = self._make_client(install['shop'], install['password'])
            transport = getattr(client, '_transport', None)
            if transport is not None:
                transport.timeout = timeout
            account = client.get_account()[0]
            if not isinstance(account, dict) or 'id' not in account:
                delay = 5
                return 'unavailable', None
            if str(account['id']) != str(install['store_id']):
                return 'invalid', None
            return 'valid', account
        except ApiError as exc:
            if exc.status in (401, 403):
                return 'invalid', None
            delay = 5
            if exc.retry_after:
                try:
                    delay = max(delay, float(exc.retry_after))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        retry_at = parsedate_to_datetime(exc.retry_after).timestamp()
                        delay = max(delay, retry_at - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            return 'unavailable', None
        except (TransportError, ResourceError):
            delay = 5
            return 'unavailable', None
        finally:
            self.store.release_verification(install['shop'], permit, delay)

    # ---- Uninstall ----

    def process_uninstall(self, shop: str, insales_id: str,
                          password: str) -> bool:
        """Обработать uninstall callback.

        InSales присылает в uninstall уже готовый пароль
        ``MD5(install_token + app_secret)`` — тот же, что был сохранён
        при install. Повторное хэширование не требуется.

        Использует атомарную операцию ``uninstall_store_or_pending_install``.

        Возвращает True при успешном удалении, False если магазин не найден.
        """
        shop = validate_shop(shop)
        return self.store.uninstall_store_or_pending_install(
            insales_id, shop, password,
        )

    # ---- Login / Autologin ----

    def create_challenge(self, store_id: str) -> tuple[str, str, str]:
        """Создать challenge для login flow.

        Возвращает (challenge_sid, nonce, challenge_sid).
        Третий элемент совпадает с первым для обратной совместимости.
        """
        store_row = self.store.get_store(store_id)
        if store_row is None:
            raise LifecycleError('Store not installed')

        challenge_sid = generate_session_id()
        nonce = generate_nonce()

        self.store.add_challenge(
            challenge_sid, store_id, nonce,
            CHALLENGE_TTL,
            generation=store_row['generation'],
        )
        return challenge_sid, nonce, challenge_sid

    def verify_autologin(self, challenge_sid: str,
                         params: dict) -> str | None:
        """Проверить autologin callback.

        Принимает challenge_sid из cookie, параметры из callback InSales.
        Возвращает новый session_id при успехе, None при ошибке.
        """
        if not challenge_sid:
            return None

        challenge = self.store.consume_challenge(challenge_sid)
        if challenge is None or challenge['expires'] < time.time():
            return None

        store_row = self.store.get_store(challenge['store_id'])
        if store_row is None:
            return None

        # Проверка поколения: challenge должен соответствовать
        # текущему поколению магазина (защита от B2/backend-2)
        if challenge.get('generation', 0) != store_row['generation']:
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
        self.store.add_session(
            sid=session_id,
            store_id=store_row['id'],
            user_id=params.get('user_id', ''),
            ttl=SESSION_TTL,
            generation=store_row['generation'],
        )
        return session_id
