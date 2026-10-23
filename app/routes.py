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
import re
import secrets
from urllib.parse import quote, urlencode

from flask import Blueprint, g, make_response, redirect, render_template, request, session

from app.middleware import SessionMiddleware, require_session
from insales.lifecycle import Lifecycle, LifecycleError
from insales.store import Store


def _parse_host(hostport: str) -> str:
    """Извлечь host из строки вида 'host', 'host:port' или '[IPv6]:port'."""
    if hostport.startswith('['):
        # [::1]:8765 → ::1
        bracket = hostport.find(']')
        if bracket != -1:
            return hostport[1:bracket]
    # 127.0.0.1:8765 → 127.0.0.1
    return hostport.split(':')[0]


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
    if dev and _is_loopback(_parse_host(shop)):
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
        except LifecycleError:
            return 'Installation verification unavailable; retry later', 503

    @blueprint.route('/login')
    def login():
        """Начало login flow: проверяет неподтверждённую установку,
        затем создаёт challenge и redirect в InSales."""
        try:
            shop = request.args.get('shop', '').lower().strip()
            insales_id = request.args.get('insales_id', '')

            store_row = store.get_store(insales_id)
            if not insales_id.isdigit():
                return 'Invalid request', 400
            if shop not in lifecycle.allowed_shops:
                return 'Store not installed', 404

            # Шаг 1: проверяем более нового кандидата (даже если магазин активен)
            # Если есть более новый кандидат с другими credentials — пробуем
            # активировать. Успех API заменяет подключение, неудача не трогает
            # старое.
            try:
                activated = lifecycle.verify_pending_install(insales_id, shop)
                if activated is None and store_row is None:
                    return 'Store not installed', 404
                if activated is not None:
                    # Активирован новый кандидат (или первый, или при живом
                    # магазине)
                    store_row = store.get_store(insales_id)
                    if store_row is None:
                        return 'Store not installed', 404
            except LifecycleError as e:
                return str(e), 502

            store_row = store.get_store(insales_id)
            if store_row is None or store_row['shop'] != shop:
                return 'Store not installed', 404
            challenge_sid, nonce, _ = lifecycle.create_challenge(insales_id)
            query = urlencode({
                'token': nonce,
                'login': base_url.rstrip('/') + '/autologin?' + urlencode({'flow': nonce}),
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
            response.set_cookie('login_' + nonce, challenge_sid, httponly=True,
                                samesite='Lax', secure=secure, max_age=300, path='/')
            return response
        except (ValueError, KeyError):
            return 'Invalid request', 400

    @blueprint.route('/autologin')
    def autologin():
        """Callback от InSales после подтверждения входа."""
        flow = request.args.get('flow', '')
        if flow and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', flow):
            return 'Login failed', 401
        cookie_name = 'login_' + flow if flow else 'challenge_sid'
        challenge_sid = request.cookies.get(cookie_name, '')
        session_id = lifecycle.verify_autologin(
            challenge_sid=challenge_sid,
            params=request.args,
        )
        if session_id is None:
            return 'Login failed', 401

        secure = _scheme(base_url) == 'https'
        opened = store.get_session(session_id)
        response = make_response(redirect('/?' + urlencode({'store_id': opened['store_id']}), 302))
        response.set_cookie(
            'app_session', session_id,
            httponly=True, samesite='Lax',
            secure=secure,
            max_age=3600, path='/',
        )
        response.delete_cookie(cookie_name, path='/')
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
            csrf_token=session.setdefault('csrf_token', secrets.token_urlsafe(32)),
        )

    @blueprint.route('/logout', methods=['POST'])
    @require_session(mw)
    def logout():
        expected = session.get('csrf_token', '')
        provided = request.form.get('csrf_token', '')
        if not expected or not secrets.compare_digest(expected, provided):
            return 'Неверный запрос. Обновите страницу приложения.', 403
        store.delete_session(g.session['sid'])
        session.clear()
        response = make_response('Вы вышли из приложения. Для входа откройте его из InSales.', 200)
        response.delete_cookie('app_session', path='/')
        return response

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
                store_id=g.store['id'],
            )
        except Exception:
            return 'Не удалось получить ответ API магазина. Повторите проверку позже.', 502

    return blueprint
