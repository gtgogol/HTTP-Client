"""Транспорт: соединение по HTTP/HTTPS и один цикл "запрос → ответ"."""

import socket
import ssl
import time

from .cookie_store import CookieJar
from .errors import ConnectionFailedError, HttpError, HttpTimeoutError
from .http_models import HttpRequest, HttpResponse
from .response_parser import _Reader, parse_response
from .url_tools import default_cookie_path, ParsedUrl

DEFAULT_TIMEOUT = 10.0


class HttpClient:
    """HTTP/1.1-клиент поверх сокетов с таймаутом и cookie-банкой."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        cookie_jar: CookieJar | None = None,
        keep_cookies: bool = True,
    ) -> None:
        if timeout is not None and timeout <= 0:
            raise HttpError("Таймаут должен быть положительным числом секунд")
        self.timeout = timeout
        self.cookie_jar = cookie_jar
        self.keep_cookies = keep_cookies

    def send(self, request: HttpRequest) -> HttpResponse:
        """Отправить готовое описание запроса и вернуть разобранный ответ."""
        parsed = request.parsed_url
        request.headers = dict(request.headers)

        if self.cookie_jar is not None and self.keep_cookies:
            if not any(name.lower() == "cookie" for name in request.headers):
                request.cookies = [
                    (cookie.name, cookie.value)
                    for cookie in self.cookie_jar.cookies_for(parsed.domain, parsed.path, parsed.secure)
                ]

        payload = request.to_bytes(parsed)
        response = self._exchange(parsed, payload, request.method)

        if self.cookie_jar is not None and self.keep_cookies:
            set_cookies = response.header_values("set-cookie")
            if set_cookies:
                self.cookie_jar.store(
                    domain=parsed.domain,
                    host_only=True,
                    default_path=default_cookie_path(parsed.path),
                    set_cookies=set_cookies,
                )
        return response

    def _exchange(self, parsed: ParsedUrl, payload: bytes, method: str) -> HttpResponse:
        """Установить HTTP-соединение, отправить запрос и получить ответ."""
        try:
            sock = socket.create_connection(
                (parsed.host, parsed.port),
                timeout=self.timeout,
            )
        except socket.timeout:
            raise HttpTimeoutError(
                f"Не удалось подключиться к {parsed.authority} за {self.timeout:g} с"
            ) from None
        except socket.gaierror as exc:
            raise ConnectionFailedError(
                f"Не удалось разрешить имя хоста {parsed.host!r}: {exc}"
            ) from None
        except OSError as exc:
            raise ConnectionFailedError(
                f"Не удалось подключиться к {parsed.authority}: {exc}"
            ) from None

        try:
            try:
                sock.sendall(payload)
                return parse_response(_Reader(sock), method)
            except socket.timeout:
                raise HttpTimeoutError(
                    f"Истёк таймаут ожидания ответа ({self.timeout:g} с)"
                ) from None
            except OSError as exc:
                raise ConnectionFailedError(
                    f"Сетевая ошибка: {exc}"
                ) from None
        finally:
            try:
                sock.close()
            except OSError:
                pass


def request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    cookie_jar: CookieJar | None = None,
) -> HttpResponse:
    """Удобная обёртка для программного использования."""
    client = HttpClient(timeout=timeout, cookie_jar=cookie_jar)
    return client.send(HttpRequest(method, url, dict(headers or {}), body))
