"""Разбор URL на компоненты, нужные для сокета и HTTP-запроса."""

from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .errors import InvalidUrlError

DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class ParsedUrl:
    scheme: str
    host: str
    port: int
    target: str  # path + query, который уходит в строку запроса
    host_header: str  # значение заголовка Host
    path: str  # путь без query, для cookie
    secure: bool
    domain: str

    @property
    def authority(self) -> str:
        return f"{self.scheme}://{self.host_header}"


def split_url(url: str) -> ParsedUrl:
    """Разобрать URL: схема, host, порт, цель запроса, значение Host."""
    raw = (url or "").strip()
    if not raw:
        raise InvalidUrlError("URL не указан")
    if "://" not in raw:
        raw = "http://" + raw

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError(f"Некорректный URL {url!r}: {exc}") from None

    scheme = parsed.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidUrlError(f"Неподдерживаемый протокол: {scheme!r}")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidUrlError("Логин и пароль в URL не поддерживаются, используйте заголовок Authorization")

    host = parsed.hostname
    if not host:
        raise InvalidUrlError(f"В URL отсутствует host: {url!r}")

    try:
        host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        raise InvalidUrlError(f"Некорректный host в URL: {host!r}") from None

    if port is None:
        port = DEFAULT_PORTS[scheme]

    path = quote(parsed.path or "/", safe="/%!$&'()*+,;=:@-._~")
    query = quote(parsed.query, safe="%!$&'()*+,;=:@/?-._~")
    target = path + (f"?{query}" if query else "")

    host_header = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        host_header = f"{host_header}:{port}"

    return ParsedUrl(
        scheme=scheme,
        host=host,
        port=port,
        target=target,
        host_header=host_header,
        path=path,
        secure=scheme == "https",
        domain=host.lower(),
    )


def default_cookie_path(path: str) -> str:
    """Путь cookie по умолчанию (RFC 6265, 5.1.4)."""
    if not path.startswith("/"):
        return "/"
    last_slash = path.rfind("/")
    return "/" if last_slash == 0 else path[:last_slash]


def merge_query(url: str, params: list[str]) -> str:
    """Добавить к URL параметры вида "ключ=значение", сохраняя существующие."""
    if not params:
        return url
    parts = urlsplit(url)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    extra: list[tuple[str, str]] = []
    for raw in params:
        if "=" not in raw:
            raise InvalidUrlError(f"Ожидался параметр вида ключ=значение, получено: {raw!r}")
        key, value = raw.split("=", 1)
        extra.append((key, value))
    query = urlencode(existing + extra, encoding="utf-8")
    return parts._replace(query=query).geturl()
