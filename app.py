#!/usr/bin/env python3
"""Совместимая точка входа. Рекомендуемый запуск: ``python run.py``.

Для production используйте WSGI-сервер:
    from app import create_app
    application = create_app()
"""
from __future__ import annotations

import sys

from app.cli import main

if __name__ == '__main__':
    # Эмулируем вызов `python -m app.cli serve`
    sys.argv = ['app.cli', 'serve']
    sys.exit(main())
