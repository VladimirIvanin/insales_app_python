"""Криптографические функции протокола InSales.

Содержит формулы подписей для install (MD5 пароля магазина)
и autologin (token/token2/token3) согласно документации InSales.

Основание: references/lifecycle.md в insales-partner-app-expert.
"""
import hashlib
import hmac
import secrets


def md5(*parts: str) -> str:
    """Lowercase hex MD5 от конкатенации строковых частей."""
    return hashlib.md5(''.join(str(p) for p in parts).encode()).hexdigest()


def store_password(install_token: str, app_secret: str) -> str:
    """Пароль магазина: MD5(INSTALL_TOKEN + APP_SECRET)."""
    return md5(install_token, app_secret)


def sign_token(nonce: str, password: str) -> str:
    """token: MD5(nonce + password)."""
    return md5(nonce, password)


def sign_token2(nonce: str, user_email: str, user_name: str,
                user_id: str, password: str) -> str:
    """token2: MD5(nonce + user_email + user_name + user_id + password)."""
    return md5(nonce, user_email, user_name, user_id, password)


def sign_token3(nonce: str, user_email: str, user_name: str,
                user_id: str, email_confirmed: str, password: str) -> str:
    """token3: MD5(nonce + user_email + user_name + user_id + email_confirmed + password).

    email_confirmed — строка 'true' или 'false' (не Python bool).
    """
    return md5(nonce, user_email, user_name, user_id, email_confirmed, password)


def secure_compare(a: str, b: str) -> bool:
    """Constant-time сравнение hex-строк. Обе должны быть 32 hex-символа."""
    if not all(re.fullmatch('[a-f0-9]{32}', s) for s in (a, b)):
        return False
    return hmac.compare_digest(a, b)


def generate_nonce() -> str:
    """Криптографически случайный nonce для challenge."""
    return secrets.token_urlsafe(32)


def generate_session_id() -> str:
    """Криптографически случайный идентификатор сессии."""
    return secrets.token_urlsafe(32)


import re  # noqa: E402 — для secure_compare
