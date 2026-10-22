# InSales Partner App (Python)

Заготовка устанавливаемого партнёрского приложения для платформы [InSales](https://www.insales.ru/) на Python и Flask.

Включает обработчики установки и удаления, вход сотрудников магазина по token3, HTTP-клиент API InSales и серверные сессии. Используйте её как основу для бизнес-логики своего приложения.

## Возможности

- **Install** — установка приложения в магазин через Basic API с фоновой верификацией
- **Login/Autologin** — вход сотрудника магазина через token3 с одноразовым challenge
- **Uninstall** — удаление приложения с атомарным отзывом доступа
- **API client** — HTTP-клиент InSales API с Basic-аутентификацией
- **Session management** — серверные сессии с привязкой к поколению установки
- **Tenant isolation** — изоляция данных магазинов на уровне middleware
- **Миграции БД** — версионирование схемы SQLite с линейными миграциями

## Подготовка приложения в InSales

1. Создайте приложение в личном кабинете InSales: **Приложения → Создать приложение**
2. Задайте **Callback URL установки**: `https://ваш-домен/install`
3. Задайте **Callback URL удаления**: `https://ваш-домен/uninstall`
4. Задайте **Callback URL входа**: `https://ваш-домен/autologin`
5. Скопируйте **App ID** и **App Secret** из личного кабинета

## Быстрый старт

```bash
cd insales_app_python

# Установить зависимости
pip install -r requirements.txt

# Настроить окружение
cp .env.example .env
# Заполните .env своими значениями

# Запустить сервер
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
│   ├── cli.py              # CLI: serve, db upgrade, db status
│   ├── middleware.py       # Сессионная middleware + tenant isolation
│   ├── routes.py           # Flask endpoints
│   ├── db/                 # Слой БД
│   │   ├── connection.py   # Подключение и миграции
│   │   └── migrations/     # SQL-миграции
│   └── templates/          # HTML-шаблоны
├── insales/                # Независимый слой InSales-протокола
│   ├── crypto.py           # MD5, подписи token/token2/token3
│   ├── client.py           # HTTP-клиент API InSales
│   ├── store.py            # SQLite Store
│   └── lifecycle.py        # Логика install/uninstall/login
└── tests/
    ├── conftest.py
    ├── test_crypto.py      # Формулы подписей
    ├── test_client.py      # HTTP-клиент
    ├── test_store.py       # Схема БД, миграции, транзакции
    └── test_lifecycle.py   # Поведенческие тесты lifecycle + routes
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

Приложение должно работать за HTTPS-terminating reverse proxy (nginx, Caddy).

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