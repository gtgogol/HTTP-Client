import socket
import ssl
from dataclasses import dataclass, field
from typing import NamedTuple
from urllib.parse import quote, urlsplit


DEFAULT_PORTS = {"http": 80, "https": 443}
USER_AGENT = "CustomPythonClient/1.0"
CRLF = b"\r\n"


class HttpError(Exception):
    """Ошибка формирования запроса или разбора ответа."""


class HttpResponse(NamedTuple):
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass
class HttpRequest:
    """Описание запроса как данных: метод, цель, заголовки, тело."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None

    # Валидация и нормализация данных
    def __post_init__(self) -> None:
        method = self.method.strip().upper()
        if not method:
            raise HttpError("Не указан метод запроса")
        self.method = method

    # Формирует сырой пакет данных для отправки через socket
    def to_bytes(self) -> bytes:
        """Собрать полный HTTP/1.1 запрос в байтах (без сокета)."""
        _, _, _, target, host_header = split_url(self.url)
        headers = build_headers(self.headers, host_header, self.body)

        lines = [f"{self.method} {target} HTTP/1.1"]
        lines.extend(f"{name}: {value}" for name, value in headers.items())
        head = "\r\n".join(lines) + "\r\n\r\n"

        data = head.encode("latin1")
        if self.body:
            data += self.body
        return data


def split_url(url: str) -> tuple[str, str, int, str, str]:
    """Разобрать URL: схема, host, порт, цель запроса, значение Host."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError(f"Неподдерживаемый протокол: {scheme}")

    host = parsed.hostname
    if not host:
        raise ValueError("В URL отсутствует host")

    port = parsed.port or DEFAULT_PORTS[scheme]

    path = quote(parsed.path or "/", safe="/%!$&'()*+,;=:@-._~")
    target = path + (f"?{parsed.query}" if parsed.query else "")

    host_header = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        host_header = f"{host_header}:{port}"

    return scheme, host, port, target, host_header


def build_headers(
    user_headers: dict[str, str], host_header: str, body: bytes | None
) -> dict[str, str]:
    """Автоматические заголовки + пользовательские поверх них."""
    defaults = {
        "Host": host_header,
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "close",
    }
    if body is not None:
        defaults["Content-Length"] = str(len(body))

    cleaned_user_headers: dict[str, str] = {}
    user_keys_lower: set[str] = set()

    for raw_name, raw_value in user_headers.items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()

        if not name or any(ch in name for ch in " :"):
            raise HttpError(f"Недопустимое имя заголовка: {raw_name!r}")
        if any(ch in value for ch in "\r\n"):
            raise HttpError(f"Недопустимое значение заголовка {name}: перенос строки")

        lowered = name.lower()
        cleaned_user_headers = {
            k: v for k, v in cleaned_user_headers.items() if k.lower() != lowered
        }
        cleaned_user_headers[name] = value
        user_keys_lower.add(lowered)

    result = {k: v for k, v in defaults.items() if k.lower() not in user_keys_lower}
    result.update(cleaned_user_headers)

    return result


class _Reader:
    """Буферизованное чтение из сокета: до разделителя, ровно N байт, до EOF."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._sock.recv(65536)
        if not chunk:
            self._eof = True
            return False
        self._buffer.extend(chunk)
        return True

    def read_until(self, delimiter: bytes) -> bytes:
        while True:
            index = self._buffer.find(delimiter)
            if index != -1:
                data = bytes(self._buffer[:index])
                del self._buffer[: index + len(delimiter)]
                return data
            if not self._fill():
                raise HttpError(
                    "Соединение закрыто раньше, чем пришёл конец заголовков"
                )

    def read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            if not self._fill():
                raise HttpError(
                    f"Тело ответа обрезано: ожидалось {size} байт, получено {len(self._buffer)}"
                )
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def read_all(self) -> bytes:
        while self._fill():
            pass
        data = bytes(self._buffer)
        self._buffer.clear()
        return data


def read_chunked(reader: _Reader) -> bytes:
    """Декодировать тело в Transfer-Encoding: chunked."""
    body = bytearray()
    while True:
        size_line = reader.read_until(CRLF)
        size_text = size_line.split(b";", 1)[0].strip()
        if not size_text:
            raise HttpError("Некорректный chunked-ответ: пустой размер чанка")
        try:
            size = int(size_text, 16)
        except ValueError:
            raise HttpError(
                f"Некорректный chunked-ответ: размер чанка {size_text!r} не hex"
            ) from None
        if size < 0:
            raise HttpError(f"Некорректный chunked-ответ: отрицательный размер {size}")

        if size == 0:
            while reader.read_until(CRLF):
                pass
            return bytes(body)

        body.extend(reader.read_exact(size))
        if reader.read_exact(2) != CRLF:
            raise HttpError("Некорректный chunked-ответ: нет CRLF после данных чанка")


def read_body(reader: _Reader, headers: dict[str, str], status_code: int) -> bytes:
    """Выбрать способ определения границы тела и прочитать само тело."""
    if 100 <= status_code < 200 or status_code in (204, 304):
        return b""

    codings = [
        coding.strip().lower()
        for coding in headers.get("transfer-encoding", "").split(",")
        if coding.strip()
    ]
    if codings and codings[-1] == "chunked":
        return read_chunked(reader)

    content_length = headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length.strip())
        except ValueError:
            raise HttpError(
                f"Некорректный Content-Length: {content_length!r}"
            ) from None
        if length < 0:
            raise HttpError(f"Некорректный Content-Length: {content_length!r}")
        return reader.read_exact(length)

    return reader.read_all()


def parse_response(reader: _Reader) -> HttpResponse:
    """Разобрать статус-строку, заголовки и тело из потока сокета."""
    head = reader.read_until(b"\r\n\r\n")
    lines = head.decode("latin1").split("\r\n")

    status_line = lines[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 2:
        raise HttpError(f"Некорректная строка статуса: {status_line!r}")
    try:
        status_code = int(parts[1])
    except ValueError:
        raise HttpError(f"Некорректный код статуса: {parts[1]!r}") from None

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise HttpError(f"Некорректный заголовок ответа: {line!r}")
        name, value = line.split(":", 1)
        key = name.strip().lower()
        val = value.strip()
        headers[key] = val if key not in headers else f"{headers[key]}, {val}"

    body = read_body(reader, headers, status_code)
    return HttpResponse(status_code, headers, body)


class HttpClient:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def send(self, request: HttpRequest) -> HttpResponse:
        """Отправить готовое описание запроса и вернуть разобранный ответ."""
        scheme, host, port, _, _ = split_url(request.url)
        payload = request.to_bytes()

        raw_sock = socket.create_connection((host, port), timeout=self.timeout)
        sock: socket.socket = raw_sock
        try:
            if scheme == "https":
                sock = ssl.create_default_context().wrap_socket(
                    raw_sock, server_hostname=host
                )
            sock.sendall(payload)
            return parse_response(_Reader(sock))
        finally:
            sock.close()

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        return self.send(HttpRequest(method, url, dict(headers or {}), body))

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        return self.request("GET", url, headers, body)
