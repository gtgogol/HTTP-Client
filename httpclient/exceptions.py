"""Объекты ошибок клиента.

Классы лежат в errors.py, модуль-фасад оставлен для обратной совместимости
с первой версией проекта (import client as ...).
"""

from .errors import (
    ConnectionFailedError,
    HttpClientError,
    HttpError,
    HttpTimeoutError,
    InvalidUrlError,
)

__all__ = [
    "ConnectionFailedError",
    "HttpClientError",
    "HttpError",
    "HttpTimeoutError",
    "InvalidUrlError",
]
