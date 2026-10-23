"""Информация о клиенте для автоматически выставляемых заголовков."""

import platform
import sys

# Продукт-токен клиента по RFC 9110 (product/version).
CLIENT_PRODUCT = "CustomPythonClient/1.0"

_PYTHON_VERSION = platform.python_version()


def default_user_agent() -> str:
    """Значение заголовка User-Agent по умолчанию."""
    return f"{CLIENT_PRODUCT} (Python/{_PYTHON_VERSION}; {sys.platform})"
