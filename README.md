# InSales Partner App (Python)

Заготовка устанавливаемого приложения для платформы [InSales](https://www.insales.ru/) на Python.

Включает полный жизненный цикл:
- **Install** — установка приложения в магазин через Basic API
- **Login/Autologin** — вход сотрудника магазина через token3
- **Uninstall** — удаление приложения с отзывом доступа
- **Webhook** — задел для приёма вебхуков

## Быстрый старт

```bash
# Клонировать репозиторий
git clone <your-repo> && cd insales_app_python

# Установить зависимости
pip install -r requirements.txt

# Настроить окружение
cp .env.example .env
# Заполнить .env своими значениями

# Запустить
python app.py
```

## Структура проекта

```
insales_app_python/
├── app.py                  # Точка входа Flask
├── requirements.txt        # Зависимости
├── .env.example            # Шаблон конфигурации
├── pyproject.toml          # Конфигурация проекта (ruff, pytest)
├── platform/               # Независимый слой InSales-протокола
│   ├── crypto.py           # MD5, подписи token/token2/token3
│   ├── client.py           # HTTP-клиент API InSales
│   ├── store.py            # SQLite хранилище
│   └── lifecycle.py        # Логика install/verify/uninstall/login
├── app/                    # Flask-слой
│   ├── middleware.py       # Сессионная middleware
│   ├── routes.py           # Flask endpoints
│   └── templates/          # HTML-шаблоны
├── tests/                  # Тесты
│   ├── conftest.py
│   ├── test_crypto.py      # Формулы подписей
│   ├── test_client.py      # HTTP-клиент
│   ├── test_lifecycle.py   # Поведенческие тесты
│   └── protocol-vectors.json
└── .github/workflows/      # CI + Security Audit
    ├── ci.yml              # Ruff lint + pytest
    └── audit.yml           # pip-audit + Trivy + CodeQL
```

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
| POST | `/webhook/insales` | Приём вебхуков (задел) |

## Тестирование

```bash
# Установить зависимости для тестов
pip install pytest pytest-cov

# Запустить тесты
python -m pytest tests/ -v

# С покрытием
python -m pytest tests/ -v --cov=platform --cov=app
```

## CI/CD

- **CI** — `ruff` линтинг + `pytest` под Python 3.10–3.13
- **Security Audit** — `pip-audit` зависимостей, `Trivy` файловой системы, `CodeQL` анализ кода

## Лицензия

MIT