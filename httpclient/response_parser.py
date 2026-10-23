"""Чтение и разбор HTTP-ответа из сокета.

Поддерживаются:
- промежуточные 1xx-ответы (RFC 9110, 15.2);
- границы тела по Transfer-Encoding: chunked, Content-Length или до EOF;
- несколько заголовков с одним именем (Set-Cookie сохраняется списком);
- распаковка gzip / deflate / br, если сервер всё же сжал тело.
"""

import gzip
import socket
import zlib

from .errors import HttpError, HttpTimeoutError
from .http_models import HttpResponse

CRLF = b"\r\n"
MAX_HEADER_BYTES = 64 * 1024


class _Reader:
    """Буферизованное чтение из сокета: до разделителя, ровно N байт, до EOF."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout:
            timeout = self._sock.gettimeout()
            raise HttpTimeoutError(
                f"Истёк таймаут ожидания ответа ({timeout:g} с)"
            ) from None
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
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise HttpError("Слишком длинная строка заголовков в ответе")
            if not self._fill():
                raise HttpError("Соединение закрыто раньше, чем пришёл конец заголовков")

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
            # Trailer-заголовки (RFC 9112, 7.1.2) читаем и отбрасываем.
            while reader.read_until(CRLF):
                pass
            return bytes(body)

        body.extend(reader.read_exact(size))
        if reader.read_exact(2) != CRLF:
            raise HttpError("Некорректный chunked-ответ: нет CRLF после данных чанка")


def read_body(
    reader: _Reader, headers: dict[str, object], status_code: int, method: str = "GET"
) -> bytes:
    """Выбрать способ определения границы тела и прочитать само тело."""
    if 100 <= status_code < 200 or status_code in (204, 304) or method == "HEAD":
        return b""

    codings = [
        coding.strip().lower()
        for coding in str(headers.get("transfer-encoding", "")).split(",")
        if coding.strip()
    ]
    if codings and codings[-1] == "chunked":
        return read_chunked(reader)

    content_length = headers.get("content-length")
    if content_length is not None:
        raw_length = str(content_length).split(",")[0].strip()
        try:
            length = int(raw_length)
        except ValueError:
            raise HttpError(f"Некорректный Content-Length: {content_length!r}") from None
        if length < 0:
            raise HttpError(f"Некорректный Content-Length: {content_length!r}")
        return reader.read_exact(length)

    # RFC 9112, 6.3: без длины тело читается до закрытия соединения.
    return reader.read_all()


def _parse_head(head: bytes, method: str) -> HttpResponse:
    """Разобрать статус-строку и заголовки (тело ещё не читается)."""
    text = head.decode("latin1")
    lines = text.split("\r\n")

    status_line = lines[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 2:
        raise HttpError(f"Некорректная строка статуса: {status_line!r}")
    try:
        status_code = int(parts[1])
    except ValueError:
        raise HttpError(f"Некорректный код статуса: {parts[1]!r}") from None
    reason = parts[2] if len(parts) > 2 else ""

    headers: dict[str, object] = {}
    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t":
            raise HttpError(f"Некорректный (свёрнутый) заголовок ответа: {line!r}")
        if ":" not in line:
            raise HttpError(f"Некорректный заголовок ответа: {line!r}")
        name, value = line.split(":", 1)
        key = name.strip().lower()
        val = value.strip()
        if not key:
            raise HttpError(f"Пустое имя заголовка ответа: {line!r}")
        if key in headers:
            existing = headers[key]
            if isinstance(existing, list):
                existing.append(val)
            else:
                headers[key] = [existing, val]
        else:
            headers[key] = val

    return HttpResponse(status_code, headers, b"", reason)

def parse_response(reader: _Reader, method: str = "GET") -> HttpResponse:
    """Разобрать статус-строку, заголовки и тело из потока сокета."""
    while True:
        response = _parse_head(reader.read_until(b"\r\n\r\n"), method)
        if not (100 <= response.status_code < 200) or response.status_code == 101:
            # 101 Switching Protocols — конечный ответ, остальные 1xx пропускаем.
            break

    body = read_body(reader, response.headers, response.status_code, method)
    return HttpResponse(
        status_code=response.status_code,
        headers=response.headers,
        raw_body=decode_content(body, response.headers),
        reason=response.reason,
    )


def decode_content(body: bytes, headers: dict[str, object]) -> bytes:
    """Распаковать тело, если сервер вернул Content-Encoding."""
    if not body:
        return body

    codings = [
        coding.strip().lower()
        for coding in str(headers.get("content-encoding", "")).split(",")
        if coding.strip()
    ]
    for coding in reversed(codings):
        if coding in ("identity", ""):
            continue
        try:
            if coding in ("gzip", "x-gzip"):
                body = gzip.decompress(body)
            elif coding == "deflate":
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
            elif coding == "br":
                body = _brotli_decompress(body)
            else:
                # Неизвестное кодирование: отдаём как есть.
                break
        except (OSError, zlib.error, ValueError) as exc:
            raise HttpError(f"Не удалось распаковать тело ({coding}): {exc}") from None
    return body


def _brotli_decompress(body: bytes) -> bytes:
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        raise HttpError(
            "Сервер ответил Brotli-сжатым телом, а модуль brotli не установлен"
        ) from None
    return brotli.decompress(body)
