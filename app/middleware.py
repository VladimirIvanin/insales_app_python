"""Flask middleware для сессий и tenant isolation.

Проверяет собственную сессию пользователя, извлекает store_id
и предоставляет InSalesClient для авторизованного запроса.

Сессия привязана к поколению (generation) магазина; если после
создания сессии произошла переустановка — сессия считается недействительной.
"""
from __future__ import annotations

from functools import wraps

from flask import g, request

from insales.client import InSalesClient
from insales.store import Store


class SessionMiddleware:
    """Middleware для управления сессиями приложения.

    Использует собственную сессию (не InSales session/token3),
    хранимую в SQLite и привязанную к HttpOnly cookie.
    """

    def __init__(self, store: Store, app_id: str, app_secret: str,
                 dev: bool = False, cookie_name: str = 'app_session'):
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.dev = dev
        self.cookie_name = cookie_name

    def get_current_session(self) -> dict | None:
        """Получить текущую сессию из SQLite по cookie."""
        sid = request.cookies.get(self.cookie_name, '')
        if not sid:
            return None
        return self.store.get_session(sid)

    def validate_session_generation(self, session: dict) -> bool:
        """Проверить, что поколение сессии соответствует текущему магазину.

        Если после создания сессии установка была заменена (новое поколение),
        сессия недействительна.
        """
        store = self.store.get_store(session['store_id'])
        if store is None:
            return False
        return store['generation'] == session.get('generation', 0)

    def get_client(self, shop: str, password: str) -> InSalesClient:
        """Создать авторизованный API-клиент для магазина."""
        return InSalesClient(
            shop=shop,
            app_id=self.app_id,
            password=password,
            dev=self.dev,
        )


def require_session(mw: SessionMiddleware):
    """Декоратор, требующий валидной сессии.

    Проверяет сессию в SQLite по cookie и соответствие поколения
    в одном согласованном чтении. При отсутствии или несоответствии
    возвращает 401.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            session = mw.get_current_session()
            if session is None:
                return '', 401
            # Получаем store в одном чтении и проверяем generation
            # на той же строке, из которой создаётся клиент.
            # Это устраняет race: если между чтениями произошла
            # переустановка, генерация не совпадёт.
            store = mw.store.get_store(session['store_id'])
            if store is None:
                return '', 401
            if store['generation'] != session.get('generation', 0):
                return '', 401
            g.session = session
            g.store = store
            g.client = mw.get_client(store['shop'], store['password'])
            return f(*args, **kwargs)
        return wrapper
    return decorator
