# InSales Partner App (Python)

Заготовка устанавливаемого партнёрского приложения для платформы [InSales](https://www.insales.ru/) на Python и Flask.

Включает обработчики установки и удаления, вход сотрудников магазина по token3, HTTP-клиент API InSales, серверные сессии, фоновую обработку webhook-уведомлений и CLI-диагностику. Используйте её как основу для бизнес-логики своего приложения.

## Возможности

- **Install** — установка приложения в магазин через Basic API с фоновой верификацией
- **Login/Autologin** — вход сотрудника магазина через token3 с одноразовым challenge
- **Uninstall** — удаление приложения с атомарным отзывом доступа
- **API client** — HTTP-клиент InSales API с Basic-аутентификацией и типизированными ресурсами
- **Webhooks** — приём уведомлений, диспетчеризация по теме, фоновый worker с повторными попытками
- **Session management** — серверные сессии с привязкой к поколению установки
- **Tenant isolation** — изоляция данных магазинов на уровне middleware
- **Миграции БД** — версионирование схемы SQLite с линейными миграциями
- **CLI** — `serve`, `db upgrade/status`, `doctor`, `webhook list/get/retry`

## Подготовка приложения в InSales

1. Создайте приложение в личном кабинете InSales: **Приложения → Создать приложение**
2. Задайте **Callback URL установки**: `https://ваш-домен/install`
3. Задайте **Callback URL удаления**: `https://ваш-домен/uninstall`
4. Задайте **Callback URL входа**: `https://ваш-домен/login`
5. Скопируйте **App ID** и **App Secret** из личного кабинета

## Быстрый старт

```bash
cd insales_app_python

# Установить зависимости
pip install -r requirements.txt

# Настроить окружение
cp .env.example .env
# Заполните .env своими значениями

# Запустить сервер (БД обновится автоматически)
python run.py

# Или через CLI:
python -m app.cli serve
```

## Настройки

Параметры конфигурации задаются через переменные окружения (файл `.env`):

| Переменная | Обязательная | Описание |
|-----------|-------------|----------|
| `INSALES_APP_ID` | Да | ID приложения из личного кабинета InSales |
| `INSALES_APP_SECRET` | Да | Secret ключ приложения |
| `APP_BASE_URL` | Да | Базовый URL для callback'ов |
| `ALLOWED_SHOPS` | Да | Список доменов магазинов через запятую |
| `LOCAL_DEMO` | Нет | `1` для HTTP на loopback (127.0.0.1) |
| `APP_DB` | Нет | Путь к файлу БД (по умолчанию `runtime/app.sqlite3`) |
| `BIND_HOST` | Нет | Адрес сервера (по умолчанию `127.0.0.1`) |
| `PORT` | Нет | Порт сервера (по умолчанию `8765`) |
| `FLASK_SECRET_KEY` | Нет | Секретный ключ Flask |
| `FLASK_ENV` | Нет | `development` или `production` |

> **Важно:** `ALLOWED_SHOPS` — обязательный параметр безопасности. Укажите явный список доменов магазинов, которым разрешено устанавливать приложение. Пустое значение не допускается.

## Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Проверка доступности |
| GET | `/install` | Callback установки от InSales |
| GET | `/login` | Начало входа (redirect в InSales) |
| GET | `/autologin` | Callback после подтверждения входа |
| GET | `/uninstall` | Callback удаления от InSales |
| GET | `/` | Dashboard (требуется сессия) |
| GET | `/probe` | Read-only проверка API магазина |
| POST | `/webhook/insales?token=<secret>` | Приём webhook-уведомлений от InSales |

## Webhook-инфраструктура

Приложение поддерживает полный цикл обработки webhook-уведомлений:

1. **Подписка** — `SubscriptionService` регистрирует стандартные темы (`orders/create`, `products/update` и т.д.) после активации магазина. Локальная запись создаётся первой, затем удалённый вебхук через API InSales.
2. **Приём** — `POST /webhook/insales?token=<callback_secret>` принимает уведомления, валидирует подпись, сохраняет в `webhook_inbox`.
3. **Диспетчеризация** — `WebhookDispatcher` маршрутизирует по теме к зарегистрированным handler'ам.
4. **Фоновый worker** — `WebhookWorker` в отдельном потоке атомарно захватывает события из inbox, диспатчит и отмечает успех/неудачу. Временные ошибки повторяются с exponential backoff до 5 попыток.

Worker запускается автоматически при старте сервера. Для ручного управления очередью используйте CLI (см. ниже).

Написание своего handler'а:

```python
# app/integrations/insales/handlers/orders.py
from app.integrations.insales.webhook_dispatcher import HandlerFunc

def handle_order_paid(store_id: str, generation: int, topic: str,
                      payload: dict) -> bool:
    # Ваша бизнес-логика
    return True  # True = успех, False = ошибка (без повтора)
```

Регистрация handler'а в `app/factory.py`:

```python
from app.integrations.insales.handlers.orders import handle_order_paid

webhook_dispatcher.register('orders/paid', handle_order_paid)
```

## CLI-команды

### Диагностика системы

```bash
# Проверка конфигурации, БД и подключения ко всем магазинам
python -m app.cli doctor
```

Команда `doctor` проверяет:
- Наличие обязательных переменных окружения
- Файл базы данных и его размер
- Статус всех миграций
- Подключение к API каждого установленного магазина (через `account.json`)

### Управление очередью webhook-событий

```bash
# Список последних 20 событий
python -m app.cli webhook list

# Список последних 50 событий
python -m app.cli webhook list 50

# Детали события
python -m app.cli webhook get 42

# Повтор сбойного события (сброс в pending)
python -m app.cli webhook retry 42
```

### База данных

```bash
# Статус миграций
python -m app.cli db status

# Применить неприменённые миграции
python -m app.cli db upgrade
```

## API-клиент InSales

Клиент построен по паттерну **Transport + Resource**. Transport отвечает за HTTP (Basic-аутентификация, обработка лимитов), Resource — за формирование правильных URL и разбор ответов. `RetryStrategy` описывает политику повторов, а конкретные сценарии (lifecycle, subscriptions) реализуют повтор при временных ошибках. Transport выполняет ровно один запрос — авто-повторы не применяются к неидемпотентным операциям.

### Примеры использования

```python
from insales.client import InSalesClient

client = InSalesClient(
    shop='shop-123.myinsales.ru',
    app_id='my-partner-app',
    password='password',
    dev=False,           # True для локальной отладки (HTTP для loopback)
)

# Аккаунт
account = client.account.get()
print(account['title'])

# Список заказов с фильтрами
orders = client.orders.list(
    page=1,
    per_page=50,
    created_at_from='2024-01-01',
    status='paid',
)

# Один заказ
order = client.orders.get(123)

# Количество по фильтрам
count = client.orders.count(status='new')

# Обновление
client.orders.update(123, {'order': {'status': 'cancelled'}})

# Товары
product = client.products.get(456)
products = client.products.list(page=1, per_page=100)

# Вебхуки
hooks = client.webhooks.list()
new_hook = client.webhooks.create(
    topic='orders/create',
    address='https://ваш-домен/webhook/insales?token=xxx',
    format_type='json',
)
client.webhooks.delete(42)
```

### Обработка ошибок

Методы ресурсов **не скрывают ошибки** — все исключения пробрасываются наверх:

```python
from insales.errors import ApiError, TransportError, ResourceError

try:
    order = client.orders.get(999)
except ApiError as exc:
    print(f'HTTP {exc.status}: {exc.body}')
except TransportError as exc:
    print(f'Network error: {exc}')
except ResourceError as exc:
    print(f'Unexpected response format: {exc}')
```

### Формирование URL

URL строятся через `_collection_path` и `_object_path()`:

| Метод | Путь |
|-------|------|
| `orders.list()` | `GET /admin/orders.json?page=1&per_page=50` |
| `orders.get(123)` | `GET /admin/orders/123.json` |
| `orders.count()` | `GET /admin/orders/count.json` |
| `orders.update(123, ...)` | `PUT /admin/orders/123.json` |
| `products.get(456)` | `GET /admin/products/456.json` |
| `webhooks.create(...)` | `POST /admin/webhooks.json` |
| `webhooks.delete(42)` | `DELETE /admin/webhooks/42.json` |

Параметры query string кодируются через `urllib.parse.urlencode` — даты с пробелами, массивы и спецсимволы обрабатываются корректно.

## БД и миграции

SQLite используется для хранения данных. Схема версионируется:

```bash
# Показать статус миграций
python -m app.cli db status

# Применить неприменённые миграции
python -m app.cli db upgrade
```

Миграции применяются автоматически при запуске сервера. Для обновления существующей БД без потери данных используйте `db upgrade`.

## Структура проекта

```
insales_app_python/
├── run.py                  # Локальный launcher
├── pyproject.toml          # Конфигурация проекта (ruff, pytest, сборка)
├── .env.example            # Шаблон конфигурации
├── app/                    # Flask-слой
│   ├── __init__.py         # Публичный create_app
│   ├── config.py           # Settings dataclass из env
│   ├── factory.py          # Фабрика Flask-приложения
│   ├── cli.py              # CLI: serve, db upgrade/status, doctor, webhook list/get/retry
│   ├── middleware.py       # Сессионная middleware + tenant isolation
│   ├── routes.py           # Flask endpoints
│   ├── webhook_routes.py   # Приём webhook-уведомлений
│   ├── db/                 # Слой БД
│   │   ├── connection.py   # Подключение и миграции
│   │   └── migrations/     # SQL-миграции
│   ├── integrations/       # Интеграции с внешними системами
│   │   └── insales/
│   │       ├── subscriptions.py     # Управление webhook-подписками
│   │       ├── webhook_dispatcher.py # Маршрутизация по теме
│   │       ├── webhook_receiver.py   # Приём и валидация
│   │       └── handlers/            # Обработчики событий
│   ├── repositories/       # Репозитории БД
│   │   ├── webhook_inbox.py
│   │   └── webhook_subscriptions.py
│   ├── workers/            # Фоновые worker'ы
│   │   └── webhooks.py    # Фоновый обработчик webhook-событий
│   └── templates/          # HTML-шаблоны
│       ├── dashboard.html
│       └── probe.html
├── insales/                # Независимый слой InSales-протокола
│   ├── crypto.py           # MD5, подписи token/token2/token3
│   ├── client.py           # Фасад API-клиента
│   ├── errors.py           # Иерархия ошибок (ApiError, TransportError, ResourceError)
│   ├── response.py         # ApiResponse dataclass
│   ├── store.py            # SQLite Store
│   ├── lifecycle.py        # Логика install/uninstall/login
│   ├── transport.py        # HTTP-транспорт с Basic-аутентификацией и RetryStrategy
│   └── resources/          # Типизированные ресурсы API
│       ├── base.py         # Resource — база с _object_path() и _build_query()
│       ├── account.py      # GET /admin/account.json
│       ├── orders.py       # list/get/count/update
│       ├── products.py     # list/get/count/update
│       └── webhooks.py     # list/get/create/update/delete
└── tests/
    ├── conftest.py
    ├── protocol-vectors.json
    ├── test_client.py      # HTTP-клиент, ресурсы, URL-сборка, ошибки
    ├── test_crypto.py      # Формулы подписей
    ├── test_store.py       # Схема БД, миграции, транзакции
    ├── test_lifecycle.py   # Поведенческие тесты lifecycle + routes
    └── test_webhooks.py    # Подписки, inbox, приём, диспетчер, worker
```

## Разработка

```bash
# Установить зависимости для тестов и разработки
pip install pytest pytest-cov ruff

# Линтинг
ruff check .

# Тесты
python -m pytest tests/ -v

# С покрытием
python -m pytest tests/ -v --cov=insales --cov=app
```

## Размещение

Для production используйте WSGI-сервер:

```python
from app import create_app
application = create_app()
```

```bash
# Gunicorn
gunicorn 'app:create_app()' --bind 0.0.0.0:8000 --workers 4

# Waitress
waitress-serve --host 0.0.0.0 --port 8000 'app:create_app()'
```

Приложение должно работать за HTTPS-terminating reverse proxy (nginx, Caddy). Webhook-уведомления от InSales приходят только на HTTPS.

## Реализованные ограничения

- **ALLOWED_SHOPS** — обязательный exact-host allowlist; пустой список запрещает все установки
- **Поколения установки** — каждая новая установка увеличивает generation; старые кандидаты и сессии не активируются
- **Транзакционная согласованность** — activate, uninstall и создание сессии выполняются в одной write-транзакции с проверкой поколения
- **HTTP только для loopback** — в dev-режиме HTTP разрешён только для 127.0.0.1/::1/localhost; внешние магазины всегда HTTPS
- **Одноразовый challenge** — nonce для autologin однократного использования с TTL
- **Повторный uninstall** — после удаления записи повторный callback возвращает 404
- **Временные ошибки API** — 429/5xx/OSError сохраняют кандидата для повтора при рестарте

## CI/CD

- **CI** — `ruff` линтинг + `pytest` под Python 3.10–3.13 (`.github/workflows/ci.yml`)
- **Security Audit** — `pip-audit` зависимостей, `Trivy` файловой системы, `CodeQL` (`.github/workflows/audit.yml`)

## Лицензия

MIT