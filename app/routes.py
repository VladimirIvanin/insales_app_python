"""Flask routes для устанавливаемого приложения InSales.

Endpoints:
- GET  /health          — проверка доступности
- GET  /install         — install callback от InSales
- GET  /login           — начало login flow (redirect в InSales)
- GET  /autologin       — callback от InSales после подтверждения
- GET  /uninstall       — uninstall callback от InSales
- GET  /                — dashboard (требует сессии)
- GET  /probe           — API probe (требует сессии)
- POST /webhook/insales — приём вебхуков (задел)
"""
import json
from urllib.parse import quote, urlencode

from flask import Blueprint, g, make_response, redirect, render_template, request

from app.middleware import SessionMiddleware, require_session
from insales.lifecycle import Lifecycle
from insales.store import Store

routes = Blueprint('app', __name__)


def create_routes(store: Store, lifecycle: Lifecycle,
                  mw: SessionMiddleware, base_url: str):
    """Создать Blueprint с настроенными зависимостями."""

    blueprint = Blueprint('app', __name__)

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
            shop = request.args.get('shop', '')
            insales_id = request.args.get('insales_id', '')

            store_row = store.get_store(insales_id)
            if store_row is None or store_row['shop'] != shop:
                return 'Store not installed', 404

            challenge_sid, nonce, _ = lifecycle.create_challenge(insales_id)
            query = urlencode({
                'token': nonce,
                'login': base_url.rstrip('/') + '/autologin',
            })
            login_url = (
                f"http://{shop}/admin/applications/"
                f"{quote(lifecycle.app_id, safe='')}"
                f"/login?{query}"
            )
            response = make_response(redirect(login_url, 302))
            response.set_cookie(
                'challenge_sid', challenge_sid,
                httponly=True, samesite='Lax',
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

        response = make_response(redirect('/', 302))
        response.set_cookie(
            'app_session', session_id,
            httponly=True, samesite='Lax',
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

            lifecycle.process_uninstall(shop, insales_id, token)
            return 'Uninstalled', 200
        except (ValueError, KeyError):
            return 'Invalid request', 400
        except Exception:
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

    @blueprint.route('/webhook/insales', methods=['POST'])
    def webhook():
        """Приём вебхуков от InSales (задел для будущей реализации).

        В production требуется:
        - определение магазина по подписке
        - проверка подписи вебхука (если предусмотрена InSales)
        - сохранение в inbox для асинхронной обработки
        """
        topic = request.headers.get('X-InSales-Topic', 'unknown')
        store_domain = request.headers.get('X-InSales-Store', 'unknown')
        body = request.get_data(as_text=True)

        # Логирование факта получения (без содержимого credentials)
        print(f'Webhook: topic={topic} store={store_domain} size={len(body)}',
              flush=True)

        # В демо-режиме просто подтверждаем получение
        return 'Webhook received', 200

    return blueprint
