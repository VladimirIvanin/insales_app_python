"""Flask routes для устанавливаемого приложения InSales.

Endpoints:
- GET  /health          — проверка доступности
- GET  /install         — install callback от InSales
- GET  /login           — начало login flow (redirect в InSales)
- GET  /autologin       — callback от InSales после подтверждения
- GET  /uninstall       — uninstall callback от InSales
- GET  /                — dashboard (требует сессии)
- GET  /probe           — API probe (требует сессии)
"""
import json
from urllib.parse import quote, urlencode

from flask import Blueprint, g, make_response, redirect, render_template, request

from app.middleware import SessionMiddleware, require_session
from insales.lifecycle import Lifecycle, LifecycleError
from insales.store import Store


def _is_loopback(host: str) -> bool:
    """Проверить, что хост — loopback (для HTTP в dev)."""
    return host in ('127.0.0.1', '::1', 'localhost')


def _scheme(base_url: str) -> str:
    """Определить схему: https если base_url начинается с https, иначе http."""
    return 'https' if base_url.startswith('https://') else 'http'


def _login_scheme(base_url: str, dev: bool, shop: str) -> str:
    """Выбрать схему для редиректа входа.

    HTTPS для всех, кроме loopback в dev-режиме.
    """
    if dev and _is_loopback(shop.split(':')[0]):
        return 'http'
    return 'https'


def create_routes(store: Store, lifecycle: Lifecycle,
                  mw: SessionMiddleware, base_url: str):
    """Создать Blueprint с настроенными зависимостями."""

    blueprint = Blueprint('app', __name__,
                          template_folder='templates')

    @blueprint.route('/health')
    def health():
        return 'ok', 200, {'Content-Type': 'text/plain'}

    @blueprint.route('/install')
    def install():
        """Install callback от InSales."""
        try:
            shop = request.args.get('shop', '')
            insales_id = request.args.get('insales_id', '')
            token = request.args.get('token', '')

            lifecycle.process_install(shop, insales_id, token)
            return 'Installation received', 200
        except (ValueError, KeyError):
            return 'Invalid request', 400

    @blueprint.route('/login')
    def login():
        """Начало login flow: создаёт challenge и redirect в InSales."""
        try:
            shop = request.args.get('shop', '').lower().strip()
            insales_id = request.args.get('insales_id', '')

            store_row = store.get_store(insales_id)
            if store_row is None or store_row['shop'] != shop:
                return 'Store not installed', 404

            challenge_sid, nonce, _ = lifecycle.create_challenge(insales_id)
            query = urlencode({
                'token': nonce,
                'login': base_url.rstrip('/') + '/autologin',
            })
            scheme = _login_scheme(base_url, lifecycle.dev, shop)
            login_url = (
                f"{scheme}://{shop}/admin/applications/"
                f"{quote(lifecycle.app_id, safe='')}"
                f"/login?{query}"
            )
            response = make_response(redirect(login_url, 302))
            secure = _scheme(base_url) == 'https'
            response.set_cookie(
                'challenge_sid', challenge_sid,
                httponly=True, samesite='Lax',
                secure=secure,
                max_age=300, path='/',
            )
            return response
        except (ValueError, KeyError):
            return 'Invalid request', 400

    @blueprint.route('/autologin')
    def autologin():
        """Callback от InSales после подтверждения входа."""
        challenge_sid = request.cookies.get('challenge_sid', '')
        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            cookie_sid=challenge_sid,
            params=request.args,
        )
        if session_id is None:
            return 'Login failed', 401

        secure = _scheme(base_url) == 'https'
        response = make_response(redirect('/', 302))
        response.set_cookie(
            'app_session', session_id,
            httponly=True, samesite='Lax',
            secure=secure,
            max_age=3600, path='/',
        )
        response.delete_cookie('challenge_sid', path='/')
        return response

    @blueprint.route('/uninstall')
    def uninstall():
        """Uninstall callback от InSales."""
        try:
            shop = request.args.get('shop', '')
            insales_id = request.args.get('insales_id', '')
            token = request.args.get('token', '')

            ok = lifecycle.process_uninstall(shop, insales_id, token)
            if ok:
                return 'Uninstalled', 200
            return 'Store not found', 404
        except (ValueError, KeyError):
            return 'Invalid request', 400
        except LifecycleError:
            return 'Uninstall error', 401

    @blueprint.route('/')
    @require_session(mw)
    def dashboard():
        """Главная страница приложения (только для авторизованных)."""
        store_row = g.store
        return render_template(
            'dashboard.html',
            title=store_row.get('title', 'Магазин'),
            shop=store_row['shop'],
            store_id=store_row['id'],
        )

    @blueprint.route('/probe')
    @require_session(mw)
    def probe():
        """Read-only проверка API магазина."""
        try:
            account, usage = g.client.get_account()
            result = {
                'api_status': 200,
                'store_id': account.get('id'),
                'usage': usage,
                'title': account.get('title'),
            }
            return render_template(
                'probe.html',
                result=json.dumps(result, indent=2, ensure_ascii=False),
            )
        except Exception as e:
            return f'API error: {e}', 502

    return blueprint
