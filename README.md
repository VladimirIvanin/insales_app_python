# InSales Partner App (Python)

Заготовка устанавливаемого партнёрского приложения для платформы [InSales](https://www.insales.ru/) на Python и Flask.

Содержит один законченный путь: установить приложение, открыть его из InSales через token3, проверить API магазина, сохранить вход после перезапуска и безопасно удалить подключение.

**Без webhook, фоновых worker'ов и очередей.** Проверка установки выполняется при первом входе, а не в фоне.

## Быстрый старт

```bash
# 1. Настроить окружение
cp .env.example .env
# Заполните INSALES_APP_ID, INSALES_APP_SECRET,
# APP_BASE_URL и ALLOWED_SHOPS в .env

# 2. Установить пакет (включает Flask, python-dotenv и CLI insales-app)
pip install -e .

# 3. Применить миграции БД
insales-app db upgrade

# 4. Запустить сервер
insales-app serve
```

Или одной командой (миграции применяются автоматически):

```bash
python run.py
```

## Подготовка приложения в InSales

1. Создайте приложение в личном кабинете InSales: **Приложения → Создать приложение**
2. Задайте **Callback URL установки**: `https://ваш-домен/install`
3. Задайте **Callback URL удаления**: `https://ваш-домен/uninstall`
4. Задайте **Callback URL входа**: `https://ваш-домен/login`
5. Скопируйте **App ID** и **App Secret** из личного кабинета

## Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Проверка доступности процесса |
| GET | `/install` | Callback установки от InSales |
| GET | `/login` | Начало входа (redirect в InSales) |
| GET | `/autologin` | Callback после подтверждения входа |
| GET | `/uninstall` | Callback удаления от InSales |
| GET | `/` | Dashboard (требуется сессия) |
| GET | `/probe` | Read-only проверка API магазина (требуется сессия) |

## Жизненный цикл

### 1. Установка (`/install`)

InSales вызывает `/install` при установке приложения в магазин. Обработчик:

1. Проверяет параметры, формат идентификатора магазина и разрешённый домен (`ALLOWED_SHOPS`)
2. Вычисляет пароль подключения по протоколу: `MD5(install_token + app_secret)`
3. Сохраняет непроверенного кандидата в SQLite
4. Отвечает `200 OK` — **без обращений к API InSales**

Кандидат не даёт доступа к API через интерфейс и не заменяет действующее подключение. Установка переживает перезапуск приложения.

### 2. Первый вход (`/login` → проверка → challenge → InSales)

При открытии приложения из InSales:

1. Если у магазина нет активного подключения — приложение находит непроверенного кандидата
2. Выполняет **один API-запрос** `account.json` для проверки credentials
3. При успехе — атомарно активирует подключение
4. После активации — обычный token3-вход (challenge + перенаправление в InSales)

**Временная ошибка API** (таймаут, 429, 5xx): кандидат сохраняется, вход показывает ошибку с возможностью повтора.

**Неверные credentials** (403, несовпадение ID): кандидат удаляется.

### 3. Повторный вход

При повторном открытии приложения (когда подключение уже активно):

- Создаётся одноразовый challenge, привязанный к браузеру
- Браузер перенаправляется в InSales для подтверждения
- InSales возвращает token3, приложение проверяет подпись
- Создаётся серверная сессия с HttpOnly cookie

### 4. Удаление (`/uninstall`)

InSales вызывает `/uninstall` при удалении приложения. Обработчик в одной транзакции:

1. Находит активное подключение или непроверенного кандидата
2. Проверяет пароль
3. Удаляет подключение, сессии, challenge и кандидатов
4. Фиксирует tombstone для предотвращения поздней активации

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
| `FLASK_SECRET_KEY` | Нет | Секретный ключ Flask (генерируется случайно, если не задан) |

> **Важно:** `ALLOWED_SHOPS` — обязательный параметр безопасности. Пустое значение не допускается.

## CLI-команды

| Команда | Назначение |
|---------|-----------|
| `insales-app serve` | Запустить web-сервер (миграции применяются автоматически) |
| `insales-app db upgrade` | Создать или обновить схему БД |
| `insales-app db status` | Статус миграций |
| `insales-app doctor` | Проверка конфигурации, БД и API магазинов |

### Примеры

```bash
# Применить миграции
insales-app db upgrade

# Статус миграций
insales-app db status

# Диагностика
insales-app doctor
```

## API-клиент InSales

```python
from insales.client import InSalesClient

client = InSalesClient(
    shop='shop-123.myinsales.ru',
    app_id='my-partner-app',
    password='password',
)

# Аккаунт
account, usage = client.get_account()
print(account['title'])

# Список заказов
orders = client.orders.list(page=1, per_page=50)

# Количество
count = client.orders.count(status='new')

# Чтение и обновление
order = client.orders.get(123)
updated = client.orders.update(123, {'order': {'status': 'cancelled'}})

# Товары
product = client.products.get(456)
products = client.products.list(page=1, per_page=100)
```

### Обработка ошибок

```python
from insales.errors import ApiError, TransportError, ResourceError

try:
    account = client.get_account()
except ApiError as exc:
    print(f'HTTP {exc.status}: {exc.body}')
except TransportError as exc:
    print(f'Network error: {exc}')
except ResourceError as exc:
    print(f'Unexpected format: {exc}')
```

## Тестирование

```bash
# Установить зависимости для тестов
pip install pytest pytest-cov ruff

# Все тесты
python -m pytest -v

# С покрытием
python -m pytest --cov=insales --cov=app

# Линтинг
ruff check .
```

## Запуск в production

```bash
# Gunicorn (предварительно: pip install gunicorn)
gunicorn 'app:create_app()' --bind 127.0.0.1:8000 --workers 1 --access-logfile -

# Waitress (предварительно: pip install waitress)
waitress-serve --call --host 127.0.0.1 --port 8000 'app:create_app'
```

**Важно:** `create_app()` накатывает миграции схемы БД при каждом запуске (идемпотентно).
Для одного экземпляра достаточно 1 worker — SQLite обеспечивает согласованность через WAL и `BEGIN IMMEDIATE`.

## Ограничения

- **Первый вход после установки** может занимать время одного API-запроса `account.json`
- **Нет фоновой проверки неоткрытых установок** — если магазин установил приложение, но ни разу не открыл, проверка не выполняется
- **Один web-процесс** — поддержка нескольких экземпляров требует отдельной настройки
- **Временная ошибка API** при проверке требует повторного входа пользователем
- **SQLite на локальном диске** — для отказоустойчивости требуется резервное копирование

## Структура проекта

```
insales_app_python/
├── run.py                  # Локальный launcher
├── pyproject.toml          # Конфигурация проекта
├── requirements.txt
├── .env.example            # Шаблон конфигурации
├── app/                    # Flask-слой
│   ├── config.py           # Settings dataclass
│   ├── factory.py          # Фабрика приложения
│   ├── cli.py              # CLI: serve, db upgrade/status, doctor
│   ├── middleware.py       # Сессии и tenant isolation
│   ├── routes.py           # HTTP endpoints
│   ├── db/
│   │   ├── connection.py   # Подключение к SQLite и миграции
│   │   └── migrations/     # SQL-миграции
│   └── templates/          # HTML-шаблоны
│       ├── dashboard.html
│       └── probe.html
├── insales/                # Слой протокола InSales
│   ├── crypto.py           # MD5, подписи token/token2/token3
│   ├── client.py           # API-клиент
│   ├── errors.py           # Иерархия ошибок
│   ├── response.py         # ApiResponse dataclass
│   ├── store.py            # SQLite хранилище
│   ├── lifecycle.py        # Install/uninstall/login
│   ├── transport.py        # HTTP-транспорт
│   └── resources/          # Ресурсы API
│       ├── base.py         # Базовый класс Resource
│       ├── account.py      # GET /admin/account.json
│       ├── orders.py       # Чтение, count, обновление заказов
│       └── products.py     # Чтение, count, обновление товаров
└── tests/
    ├── conftest.py
    ├── protocol-vectors.json
    ├── test_client.py      # HTTP-клиент и ресурсы
    ├── test_crypto.py      # Формулы подписей
    ├── test_store.py       # SQLite схема и миграции
    └── test_lifecycle.py   # Поведенческие и интеграционные тесты
```

## Лицензия

MIT