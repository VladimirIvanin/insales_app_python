"""Конфигурация приложения из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Валидированные настройки приложения, однократно загруженные из env."""

    app_id: str
    app_secret: str
    base_url: str
    dev: bool
    allowed_shops: frozenset[str]
    db_path: Path
    bind_host: str
    port: int
    flask_secret_key: str
    flask_debug: bool

    # База для относительных путей .env/APP_DB
    runtime_dir: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def from_env(cls, env_path: str | None = None) -> Settings:
        """Загрузить .env и создать Settings из os.environ.

        Args:
            env_path: Явный путь к .env. Если None — ищет .env в CWD.
        """
        from dotenv import load_dotenv

        dotenv_path = Path(env_path or '.env')
        if dotenv_path.exists():
            load_dotenv(dotenv_path, override=False)

        runtime_dir = Path(os.environ.get('APP_RUNTIME_DIR', 'runtime')).resolve()

        # ALLOWED_SHOPS — обязателен, не пустой
        allowed_raw = os.environ.get('ALLOWED_SHOPS', '').strip()
        if not allowed_raw:
            raise ValueError(
                'ALLOWED_SHOPS must be set to a comma-separated list of '
                'allowed shop domains. An empty value is not allowed '
                'for security reasons.'
            )
        allowed_shops = frozenset(
            s.strip().lower()
            for s in allowed_raw.split(',')
            if s.strip()
        )

        if not allowed_shops:
            raise ValueError(
                'ALLOWED_SHOPS must contain at least one domain.'
            )

        db_raw = os.environ.get('APP_DB', 'app.sqlite3')
        # Если относительный — резолвим от runtime_dir
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = runtime_dir / db_raw

        return cls(
            app_id=os.environ['INSALES_APP_ID'],
            app_secret=os.environ['INSALES_APP_SECRET'],
            base_url=os.environ['APP_BASE_URL'].rstrip('/'),
            dev=os.environ.get('LOCAL_DEMO') == '1',
            allowed_shops=allowed_shops,
            db_path=db_path,
            bind_host=os.environ.get('BIND_HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '8765')),
            flask_secret_key=os.environ.get(
                'FLASK_SECRET_KEY', os.urandom(32).hex(),
            ),
            flask_debug=os.environ.get('FLASK_DEBUG', '0') == '1',
            runtime_dir=runtime_dir,
        )
