class HttpClientError(Exception):
    """Базовый класс всех ошибок клиента."""


class HttpError(HttpClientError):
    """Ошибка формирования запроса или разбора ответа."""


class HttpTimeoutError(HttpClientError):
    """Сервер не ответил за отведённое время."""


class ConnectionFailedError(HttpClientError):
    """Не удалось установить сетевое соединение."""


class InvalidUrlError(HttpError, ValueError):
    """URL не может быть отправлен (нет схемы, host и т.п.)."""
