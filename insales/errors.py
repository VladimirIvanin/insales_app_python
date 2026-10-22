"""Иерархия ошибок API InSales и транспорта."""


class InSalesError(Exception):
    """Базовое исключение всех ошибок, связанных с InSales."""


class TransportError(InSalesError):
    """Ошибка HTTP-транспорта (сетевые проблемы, таймауты)."""


class ApiError(InSalesError):
    """Ошибка API InSales (неуспешный HTTP-статус).

    Атрибуты:
        status: HTTP-статус ответа.
        body: Тело ответа (если есть).
        usage: Заголовок API-Usage-Limit.
        retry_after: Заголовок Retry-After (значение в секундах).
    """

    def __init__(self, status: int, body: str = '',
                 usage: str | None = None,
                 retry_after: str | None = None):
        self.status = status
        self.body = body
        self.usage = usage
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [f'HTTP {self.status}']
        if self.usage:
            parts.append(f'usage={self.usage}')
        if self.retry_after:
            parts.append(f'retry_after={self.retry_after}')
        return ' | '.join(parts)

    @property
    def is_temporary(self) -> bool:
        """429, 5xx — временные ошибки, допускающие повтор."""
        return self.status in (429,) or self.status >= 500


class ResourceError(InSalesError):
    """Ошибка уровня ресурса (неверные параметры, неожиданный формат)."""


class ResponseTooLargeError(ApiError):
    """Ответ API превысил допустимый размер."""
