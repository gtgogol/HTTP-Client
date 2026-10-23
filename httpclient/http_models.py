"""HTTP-модели и автоматическое формирование заголовков запроса.

Заголовки выставляются в соответствии с требованиями RFC 9110/9112:
- обязательный Host (несколько Host запрещены);
- User-Agent с токеном продукта;
- Date в формате IMF-fixdate (RFC 9110, 5.6.7);
- Accept, Accept-Encoding, Connection;
- Content-Length для тела; Content-Type выставляет слой body.py;
- Cookie из cookie-банки одним заголовком.
Явно переданные пользователем заголовки главнее автоматических,
сравнение имён — регистронезависимое.
"""

from dataclasses import dataclass, field
from email.utils import formatdate
from typing import NamedTuple

from .errors import HttpError
from .url_tools import ParsedUrl, split_url
from .user_agent import default_user_agent

DEFAULT_VERSION = "HTTP/1.1"
SUPPORTED_VERSIONS = ("HTTP/1.0", "HTTP/1.1")

# RFC 9110, 5.6.2: tchar
_TCHAR = frozenset(
    "!#$%&'*+-.^_`|~0123456789"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class HttpResponse(NamedTuple):
    status_code: int
    headers: dict[str, object]
    raw_body: bytes
    reason: str = ""

    @property
    def body(self) -> bytes:
        """Тело ответа как bytes."""
        return self.raw_body

    @property
    def text(self) -> str:
        """Тело как текст с учётом charset из Content-Type."""
        charset = "utf-8"
        content_type = self.header("content-type") or ""
        for param in content_type.split(";")[1:]:
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "charset" and value.strip():
                charset = value.strip().strip('"')
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        """Первое значение заголовка; имена регистронезависимы."""
        value = self.headers.get(name.strip().lower())
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def header_values(self, name: str) -> list[str]:
        """Все значения заголовка в порядке получения (нужно для Set-Cookie)."""
        value = self.headers.get(name.strip().lower())
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value]


@dataclass
class HttpRequest:
    """Описание запроса как данных: метод, цель, заголовки, тело."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    cookies: list[tuple[str, str]] = field(default_factory=list)
    version: str = DEFAULT_VERSION

    # Валидация и нормализация данных
    def __post_init__(self) -> None:
        method = self.method.strip().upper()
        # RFC 9110, 5.6.2: метод — непустой токен из ASCII.
        if not method or any(ch not in _TCHAR for ch in method):
            raise HttpError(f"Некорректный метод запроса: {self.method!r}")
        self.method = method

        if self.version not in SUPPORTED_VERSIONS:
            raise HttpError(f"Неподдерживаемая версия HTTP: {self.version!r}")
        if self.body is not None and not isinstance(self.body, bytes):
            raise HttpError("Тело запроса должно быть bytes")

    @property
    def parsed_url(self) -> ParsedUrl:
        return split_url(self.url)

    # Формирует сырой пакет данных для отправки через socket
    def to_bytes(self, parsed: ParsedUrl | None = None) -> bytes:
        """Собрать полный HTTP/1.1 запрос в байтах (без сокета)."""
        parsed = parsed or self.parsed_url
        headers = build_headers(self.headers, parsed, self.body, self.cookies)
        lines = [f"{self.method} {parsed.target} {self.version}"]
        lines.extend(f"{name}: {value}" for name, value in headers.items())
        head = "\r\n".join(lines) + "\r\n\r\n"

        data = head.encode("latin1")
        if self.body:
            data += self.body
        return data


def _normalize_header(name: object, value: object) -> tuple[str, str]:
    name = str(name).strip()
    raw_value = str(value)
    # RFC 9110, 5.5: obs-fold не поддерживаем, значение — одна строка.
    if "\r" in raw_value or "\n" in raw_value:
        raise HttpError(f"Недопустимое значение заголовка {name}: перенос строки")
    value = raw_value.strip()

    if not name:
        raise HttpError("Пустое имя заголовка")
    if any(ch not in _TCHAR for ch in name):
        raise HttpError(f"Недопустимое имя заголовка: {name!r}")
    if "\x00" in value:
        raise HttpError(f"Недопустимое значение заголовка {name}: нулевой байт")
    if any(ord(ch) < 0x20 and ch != "\t" for ch in value):
        raise HttpError(f"Недопустимые символы в значении заголовка {name}")

    try:
        value.encode("latin1")
    except UnicodeEncodeError:
        raise HttpError(
            f"Значение заголовка {name} содержит символы вне ISO-8859-1; "
            "передавайте их в закодированном виде (RFC 8187)"
        ) from None
    return name, value


def build_headers(
    user_headers: dict[str, str],
    parsed: ParsedUrl,
    body: bytes | None,
    cookies: list[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """Автоматические заголовки + пользовательские поверх них (без дублей)."""
    cleaned_user_headers: dict[str, str] = {}
    for raw_name, raw_value in user_headers.items():
        name, value = _normalize_header(raw_name, raw_value)
        lowered = name.lower()
        if lowered == "host":
            name = "Host"
        # Регистронезависимая замена в пределах пользовательских заголовков.
        cleaned_user_headers = {
            k: v for k, v in cleaned_user_headers.items() if k.lower() != lowered
        }
        cleaned_user_headers[name] = value

    user_keys_lower = {name.lower() for name in cleaned_user_headers}

    defaults: dict[str, str] = {
        "Host": parsed.host_header,
        "User-Agent": default_user_agent(),
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Date": formatdate(usegmt=True),
    }
    if body is not None:
        # RFC 9110, 8.6: Content-Length обязателен для тела фиксированной длины.
        defaults["Content-Length"] = str(len(body))

    # Cookie — один заголовок, склеенный через "; " (RFC 6265, 5.4).
    if cookies and "cookie" not in user_keys_lower:
        defaults["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies)

    if "host" in user_keys_lower:
        host_value = next(v for k, v in cleaned_user_headers.items() if k.lower() == "host")
        if not host_value:
            raise HttpError("Заголовок Host не может быть пустым")
    else:
        # RFC 9112, 3.2: Host обязателен; ставим его первым.
        cleaned_user_headers = {"Host": defaults.pop("Host"), **cleaned_user_headers}
        user_keys_lower.add("host")

    result = {k: v for k, v in defaults.items() if k.lower() not in user_keys_lower}
    result.update(cleaned_user_headers)
    return result
