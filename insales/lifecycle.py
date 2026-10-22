"""Жизненный цикл устанавливаемого приложения InSales.

Содержит логику обработки install callback (приём кандидата,
фоновая верификация через account API, активация), uninstall
и утилиты для login/autologin.
"""
import secrets
import time

from insales.client import ApiError, InSalesClient, validate_shop
from insales.crypto import (
    generate_nonce,
    generate_session_id,
    secure_compare,
    sign_token3,
    store_password,
)
from insales.store import Store

INSTALL_WORKERS = 2
CHALLENGE_TTL = 300  # 5 минут
SESSION_TTL = 3600  # 1 час


class LifecycleError(Exception):
    """Ошибка жизненного цикла приложения."""


class Lifecycle:
    """Жизненный цикл устанавливаемого приложения InSales."""

    def __init__(self, store: Store, app_id: str, app_secret: str,
                 dev: bool = False):
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.dev = dev

    def process_install(self, shop: str, insales_id: str,
                        token: str) -> str:
        """Обработать install callback.

        1. Валидировать параметры
        2. Вычислить пароль магазина
        3. Сохранить кандидата
        4. Запустить фоновую верификацию

        Возвращает candidate_id (для логирования/отладки).
        """
        shop = validate_shop(shop)
        if not insales_id.isdigit():
            raise ValueError(f'Invalid insales_id: {insales_id}')
        if not token:
            raise ValueError('Empty install token')

        password = store_password(token, self.app_secret)
        candidate = secrets.token_urlsafe(32)

        self.store.add_install(candidate, insales_id, shop, password)
        return candidate

    def _make_client(self, shop: str, password: str) -> InSalesClient:
        """Создать клиент API для магазина. Выделено для тестирования."""
        return InSalesClient(shop, self.app_id, password, dev=self.dev)

    def verify_install(self, candidate: str) -> bool:
        """Фоновая верификация кандидата установки.

        Проверяет, что магазин доступен по API и account.id совпадает.
        При успехе активирует подключение, при неудаче удаляет кандидата.
        Возвращает True при успешной активации.
        """
        install = self.store.get_install(candidate)
        if install is None:
            return False

        client = self._make_client(install['shop'], install['password'])

        try:
            account, _ = client.get_account()
            if str(account.get('id')) != str(install['store_id']):
                return False

            # Атомарная активация: заменяем существующее подключение
            self.store.delete_store_sessions(install['store_id'])
            self.store.delete_store_challenges(install['store_id'])
            self.store.add_store(
                store_id=install['store_id'],
                shop=install['shop'],
                password=install['password'],
                title=account.get('title', 'Store'),
            )
            self.store.delete_install(candidate)
            return True

        except (ApiError, OSError, ValueError):
            self.store.delete_install(candidate)
            return False

    def process_uninstall(self, shop: str, insales_id: str,
                          token: str) -> bool:
        """Обработать uninstall callback.

        Проверяет пароль магазина, удаляет подключение и все связанные данные.
        Возвращает True при успешном удалении.
        """
        shop = validate_shop(shop)
        store_row = self.store.get_store(insales_id)
        if store_row is None:
            return False
        if store_row['shop'] != shop:
            raise ValueError('Shop mismatch')

        if not secure_compare(token, store_row['password']):
            raise LifecycleError('Invalid uninstall token')

        self.store.delete_store_sessions(insales_id)
        self.store.delete_store_challenges(insales_id)
        self.store.delete_install(insales_id)
        self.store.delete_store(insales_id)
        return True

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
        self.store.add_session(
            sid=session_id,
            store_id=store_row['id'],
            user_id=params.get('user_id', ''),
            ttl=SESSION_TTL,
        )
        return session_id
