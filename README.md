# InSales Partner App (Python)

Заготовка устанавливаемого партнёрского приложения для платформы [InSales](https://www.insales.ru/) на Python и Flask.

Содержит один законченный путь: установить приложение, открыть его из InSales через token3, проверить API магазина, сохранить вход после перезапуска и безопасно удалить подключение.

## Требования

- Python ≥ 3.10
- `pip` и `venv` (рекомендуется)

## Быстрый старт

Инструкция предполагает, что у вас есть доступный платформе InSales HTTPS-адрес (через ngrok, локальный туннель или выделенный сервер). Этот адрес указывается в `APP_BASE_URL` — по нему InSales будет вызывать callback'и установки, входа и удаления.

```bash
# 0. Подготовить окружение
python3 -m venv .venv
source .venv/bin/activate

# 1. Настроить конфигурацию
cp .env.example .env
# Заполните INSALES_APP_ID, INSALES_APP_SECRET, APP_BASE_URL, ALLOWED_SHOPS

# 2. Установить пакет (включает Flask, python-dotenv и CLI insales-app)
pip install -e .

# 3. Применить миграции БД
insales-app db upgrade

# 4. Запустить сервер
insales-app serve
```

После запуска проверьте доступность:

```bash
curl http://127.0.0.1:8765/health
# → ok
```

Или одной командой (миграции применяются автоматически):

```bash
python run.py
```

> **Важно:** при локальной разработке на 127.0.0.1 платформа InSales не сможет вызвать callback'и. Используйте туннель (ngrok, bore) или выделенный сервер с HTTPS. Флаг `LOCAL_DEMO=1` разрешает HTTP-соединение к самому магазину/симулятору на loopback-адресе; для запуска приложения на localhost за HTTPS-туннелем он не требуется.

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
3. Сохраняет непроверенного кандидата в SQLite с доменом и `insales_id`; квота считается по домену
4. Отвечает `200 OK` — **без обращений к API InSales**

Кандидат не даёт доступа к API через интерфейс и не заменяет действующее подключение. Установка переживает перезапуск приложения.

Число непроверенных кандидатов на один домен ограничено (по умолчанию 5). Просроченные кандидаты (7 дней) удаляются при приёме нового. При переполнении применяется проверка, описанная ниже; произвольного вытеснения нет.

### 2. Первый вход (`/login` → проверка → challenge → InSales)

При открытии приложения из InSales:

1. Если есть непроверенный кандидат — выполняется **один API-запрос** `account.json`
2. При успехе — атомарно активирует подключение (заменяет предыдущее, если оно было)
3. Кандидата нет — вход продолжается по существующему активному подключению
4. После активации — обычный token3-вход (challenge + перенаправление в InSales)

**Временная ошибка API** (таймаут, 429, 5xx): кандидат сохраняется. Если есть активное подключение — вход продолжается по нему (временная ошибка не блокирует доступ).

**Неверные credentials** (403, несовпадение ID): кандидат удаляется, но активное подключение (если было) продолжает работать.

### 3. Повторный вход

При повторном открытии приложения (когда подключение уже активно):

- Создаётся одноразовый challenge, привязанный к браузеру и поколению магазина
- Браузер перенаправляется в InSales для подтверждения
- InSales возвращает token3, приложение проверяет подпись
- Создаётся серверная сессия с HttpOnly cookie

Сессия привязана к поколению магазина; после переустановки старые сессии становятся недействительными.

### 4. Удаление (`/uninstall`)

InSales вызывает `/uninstall` при удалении приложения. Обработчик в одной транзакции:

1. Находит активное подключение **или** непроверенного кандидата с совпадающим паролем
2. Проверяет пароль
3. Удаляет подключение (если пароль совпал), сессии, challenge и кандидатов
4. Фиксирует tombstone для предотвращения поздней активации

Если у магазина есть активное подключение, но uninstall пришёл с паролем от другого (параллельного) кандидата — удаляются только кандидаты с этим паролем, активное подключение не трогается. Uninstall без активного подключения удаляет только pending install.

## Настройки

Параметры конфигурации задаются через переменные окружения (файл `.env`). Поиск `.env` выполняется от `CWD` (текущей рабочей директории).

| Переменная | Обязательная | Описание |
|-----------|-------------|----------|
| `INSALES_APP_ID` | Да | ID приложения из личного кабинета InSales |
| `INSALES_APP_SECRET` | Да | Secret ключ приложения |
| `APP_BASE_URL` | Да | Базовый URL для callback'ов InSales (HTTPS для production) |
| `ALLOWED_SHOPS` | Да | Список доменов магазинов через запятую |
| `LOCAL_DEMO` | Нет | `1` для HTTP на loopback (127.0.0.1) |
| `APP_DB` | Нет | Путь к файлу БД (по умолчанию `app.sqlite3`; итоговый путь — `{APP_RUNTIME_DIR}/app.sqlite3`; абсолютный путь используется как есть) |
| `APP_RUNTIME_DIR` | Нет | Директория для runtime-файлов (по умолчанию `runtime` от CWD) |
| `BIND_HOST` | Нет | Адрес сервера (по умолчанию `127.0.0.1`) |
| `PORT` | Нет | Порт сервера (по умолчанию `8765`) |
| `FLASK_SECRET_KEY` | Нет | Секретный ключ Flask (генерируется случайно, если не задан) |

> **Важно:** `ALLOWED_SHOPS` — обязательный параметр безопасности. Пустое значение не допускается.

> **О путях:** `APP_DB` может быть абсолютным или относительным. Относительный путь резолвится от `APP_RUNTIME_DIR` (по умолчанию `runtime/` в CWD). По умолчанию `APP_DB=app.sqlite3`, а `APP_RUNTIME_DIR=runtime`, поэтому финальный путь — `runtime/app.sqlite3`. Если задать абсолютный путь (`/data/db.sqlite3`), `APP_RUNTIME_DIR` не влияет.

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

Пароль для API вычисляется один раз при установке (через `store_password`) и сохраняется в БД. После установки `g.client` доступен в любом `@require_session`-обработчике. При необходимости создать клиент вручную:

```python
from insales.crypto import store_password
from insales.client import InSalesClient

# Пароль магазина: MD5(install_token + app_secret)
password = store_password(install_token, app_secret)

client = InSalesClient(
    shop='shop-123.myinsales.ru',
    app_id='my-partner-app',
    password=password,
)

# Аккаунт и использование API
account, usage = client.get_account()
print(account['title'], usage)
print(f"ID магазина: {account['id']}")
```

### Ресурсы

```python
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

### Расширение: собственный ресурс API

```python
from insales.resources.base import Resource
from insales.transport import Transport

class MyCustomResource(Resource):
    _collection_path = '/admin/my_custom.json'

# Использование:
transport = Transport(shop='shop-123.myinsales.ru',
                      app_id='my-partner-app',
                      password='password')
resource = MyCustomResource(transport)
response = resource._get(resource._collection)
# response.data — список объектов
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
gunicorn 'app:create_app()' --bind 127.0.0.1:8000 --workers 1 \
  --access-logfile - \
  --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s" %(s)s %(b)s'

# Waitress (предварительно: pip install waitress)
waitress-serve --call --host 127.0.0.1 --port 8000 'app:create_app'
```

> **Важно:** в access log формате используется `%(U)s` (путь без query) вместо стандартного `%(r)s` (RAW_URI), чтобы избежать записи query-параметров callback'ов (install_token, token3 и др.) в логи.

`create_app()` накатывает миграции схемы БД при каждом запуске (идемпотентно). Для одного экземпляра достаточно 1 worker — SQLite обеспечивает согласованность через WAL и `BEGIN IMMEDIATE`.

## Ограничения

- **Первый вход после установки** может занимать время одного API-запроса `account.json`
- **Нет фоновой проверки неоткрытых установок** — если магазин установил приложение, но ни разу не открыл, проверка не выполняется
- **Один web-процесс** — поддержка нескольких экземпляров требует отдельной настройки
- **Временная ошибка API** при проверке требует повторного входа пользователем (если нет активного подключения)
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

### Восстановление установки и вкладки браузера

При заполнении очереди приложение проверяет уже сохранённых кандидатов через API
в пределах двух секунд. Место освобождается только после `401`/`403` или
подтверждённого несовпадения `account.id`. Подтверждённые credentials и кандидаты
с временной ошибкой сохраняются; если места нет, callback отвечает `503` для
повторной доставки. Сетевые запросы выполняются вне транзакции записи.

SQLite хранит общий бюджет проверок домена: до 10 обращений за минуту и одно
одновременное обращение с арендой на 15 секунд. Временная ошибка откладывает
следующую проверку минимум на 5 секунд; `Retry-After` может увеличить паузу.
Перезапуск приложения сохраняет бюджет и кандидатов. Некорректный ответ `200`
считается временной ошибкой, а не основанием удалить credentials.

Проверенный `account.id` позволяет сохранить подключение после смены домена.
Активация нового владельца домена отзывает сессии прежнего подключения.
Challenge каждого входа выбирается параметром `flow` и остаётся связанным с
браузером: два начатых входа могут завершаться независимо.
В одном профиле браузера активен один магазин. Страницы и формы передают
ожидаемый `store_id`; после переключения магазина старая вкладка получает `409`.
Этот параметр проверяет контекст и не даёт доступа к чужому магазину.
