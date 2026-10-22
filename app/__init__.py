"""Пакет Flask-приложения партнёрского InSales-приложения.

Публичная фабрика ``create_app`` находится в ``app.factory``.
"""

from app.factory import create_app

__all__ = ['create_app']
