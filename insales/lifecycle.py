"""Жизненный цикл устанавливаемого приложения InSales.

Содержит логику обработки install callback (приём кандидата,
фоновая верификация через account API, активация), uninstall
и утилиты для login/autologin.
"""
from __future__ import annotations

import secrets
import time
from concurrent.futures import ThreadPoolExecutor

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
    """Жизненный цикл устанавливаемого приложения InSales.

    Создание (``__init__``) только сохраняет конфигурацию.
    Для запуска worker'ов и восстановления pending установок
    необходимо вызвать ``start()``.
    """

    def __init__(self, store: Store, app_id: str, app_secret: str,
                 dev: bool = False,
                 allowed_shops: set[str] | None = None):
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.dev = dev
        self.allowed_shops = allowed_shops or set()
        # Запрещаем пустой allowlist — используется в store.py
        # (проверяется также в app/config.py)
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

        password = store_password(token, self.app_secret)
        candidate = secrets.token_urlsafe(32)

        # Поколение: монотонный счётчик из БД
        with self.store._connect() as conn:  # noqa: SLF001
            generation = self.store.next_generation(conn)

        self.store.add_install(candidate, insales_id, shop, password,
                               generation=generation)
        if self._workers is not None:
            self._workers.submit(self._verify_install_worker, candidate)
        return candidate

    def _make_client(self, shop: str, password: str) -> InSalesClient:
        """Создать клиент API для магазина. Выделено для тестов."""
        return InSalesClient(shop, self.app_id, password, dev=self.dev)

    def _verify_install_worker(self, candidate: str) -> None:
        """Фоновая верификация кандидата установки."""
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
                pass  # активировано
            return

        except ApiError as exc:
            if exc.is_temporary:
                # Временная ошибка — оставляем кандидата для повтора при рестарте
                return
            self.store.delete_install(candidate)
            return
        except (OSError, ValueError):
            # Сетевой обрыв, невалидный JSON — временные, оставляем кандидата
            return
        except Exception:
            self.store.delete_install(candidate)
            return

    # ---- Uninstall ----

    def process_uninstall(self, shop: str, insales_id: str,
                          token: str) -> bool:
        """Обработать uninstall callback.

        Валидирует shop/пароль, затем атомарно удаляет магазин,
        сессии, challenge'и и кандидатов в одной write-транзакции.

        Если магазин ещё не активирован (есть только pending install),
        удаляет кандидата по паролю.

        Возвращает True при успешном удалении, False если магазин не найден.
        """
        shop = validate_shop(shop)

        # Сначала проверяем, может это ещё не активированный кандидат?
        store_row = self.store.get_store_shop_and_password(insales_id)
        if store_row is None:
            # Магазина нет — возможно, есть только pending install?
            install = self.store.get_install_by_store_id(insales_id)
            if install is not None:
                if (
                    install['shop'] == shop
                    and secure_compare(token, install['password'])
                ):
                    # Атомарно удаляем этот кандидат
                    return self.store.consume_install_by_store_password(
                        insales_id, install['password']
                    )
                raise LifecycleError('Invalid uninstall token for pending install')
            return False

        if store_row['shop'] != shop:
            raise ValueError('Shop mismatch')

        if not secure_compare(token, store_row['password']):
            raise LifecycleError('Invalid uninstall token')

        # Единая транзакция удаления с проверкой generation
        return self.store.uninstall_store(insales_id,
                                          store_row['generation'])

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
