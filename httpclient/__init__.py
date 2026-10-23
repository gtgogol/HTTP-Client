"""Учебный HTTP/1.1-клиент на стандартной библиотеке Python."""

__version__ = "1.0.0"

from .body import PreparedBody, encode_form, encode_json, encode_text, read_body_file
from .client import DEFAULT_TIMEOUT, HttpClient, request
from .config import ClientConfig
from .cookie_store import CookieJar, StoredCookie
from .errors import (
    ConnectionFailedError,
    HttpClientError,
    HttpError,
    HttpTimeoutError,
    InvalidUrlError,
)
from .http_models import HttpRequest, HttpResponse
from .url_tools import ParsedUrl, split_url

__all__ = [
    "ClientConfig",
    "ConnectionFailedError",
    "CookieJar",
    "DEFAULT_TIMEOUT",
    "HttpClient",
    "HttpClientError",
    "HttpError",
    "HttpRequest",
    "HttpResponse",
    "HttpTimeoutError",
    "InvalidUrlError",
    "ParsedUrl",
    "PreparedBody",
    "StoredCookie",
    "encode_form",
    "encode_json",
    "encode_text",
    "read_body_file",
    "request",
    "split_url",
    "__version__",
]
