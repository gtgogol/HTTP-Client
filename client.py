import socket
import ssl
from typing import NamedTuple
from urllib.parse import urlsplit


class HttpResponse(NamedTuple):
    status_code: int
    headers: dict[str, str]
    body: bytes


class HttpClient:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def get(self, url: str) -> HttpResponse:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Неподдерживаемый протокол: {scheme}")

        host = parsed.hostname
        if not host:
            raise ValueError("В URL отсутствует host")
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path if parsed.path else "/"
        if parsed.query:
            path += f"?{parsed.query}"

        raw_sock = socket.create_connection((host, port), timeout=self.timeout)
        if scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw_sock, server_hostname=host)
        else:
            sock = raw_sock

        try:
            request_text = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: CustomPythonClient/1.0\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            sock.sendall(request_text.encode("latin1"))

            buffer = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
        finally:
            sock.close()

        return self._parse_response(buffer)

    def _parse_response(self, raw_data: bytearray) -> HttpResponse:
        separators = b"\r\n\r\n"
        index = raw_data.find(separators)
        if index == -1:
            raise ValueError("Некорректный ответ: отсутствуют заголовки")

        header_bytes = raw_data[:index]
        body = bytes(raw_data[index + len(separators) :])

        lines = header_bytes.decode("latin1").split("\r\n")

        status_line = lines[0]
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            raise ValueError("Некорректная строка статуса")

        status_code = int(parts[1])

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, val = line.split(":", 1)
                headers[name.strip().lower()] = val.strip()

        return HttpResponse(status_code, headers, body)
