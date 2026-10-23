"""Хранилище cookie-банки на диске.

Формат текстовый и совместим с Netscape cookies.txt (его понимают curl
и большинство HTTP-инструментов), плюс атрибуты Secure / HttpOnly
добавляются в конец строки.

Строка: domain \\t flag \\t path \\t secure \\t expires \\t name \\t value \\t httponly
"""

import time
import calendar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import HttpError


@dataclass
class StoredCookie:
    domain: str  # без ведущей точки, в нижнем регистре
    host_only: bool  # True, если домен взят из ответа без Domain=...
    path: str
    secure: bool
    expires: float | None  # None = сессионная cookie
    name: str
    value: str
    http_only: bool = False

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires is None:
            return False
        return self.expires <= (time.time() if now is None else now)

    def matches(self, host: str, path: str, secure: bool) -> bool:
        """Подходит ли cookie для данного запроса (RFC 6265, 5.4)."""
        host = host.lower()
        if self.host_only:
            if host != self.domain:
                return False
        elif not (host == self.domain or host.endswith("." + self.domain)):
            return False
        if self.secure and not secure:
            return False
        return path_matches(path, self.path)

    def to_line(self) -> str:
        expires = 0 if self.expires is None else int(self.expires)
        return "\t".join(
            [
                self.domain,
                "FALSE",  # include_subdomains
                self.path,
                "TRUE" if self.secure else "FALSE",
                str(expires),
                self.name,
                self.value,
                "TRUE" if self.http_only else "FALSE",
            ]
        )


def path_matches(request_path: str, cookie_path: str) -> bool:
    """RFC 6265, 5.1.4: проверка попадания пути запроса под путь cookie."""
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"


def _normalize_expires(raw: str) -> float | None:
    value = int(raw)
    return None if value == 0 else float(value)


class CookieJar:
    """Набор cookie + отсев просроченных и удалённых."""

    def __init__(self, cookies: Iterable[StoredCookie] = ()) -> None:
        self._cookies: list[StoredCookie] = list(cookies)

    def __len__(self) -> int:
        return len(self._cookies)

    def __iter__(self):
        return iter(self._cookies)

    def clear(self) -> None:
        self._cookies.clear()

    def purge_expired(self, now: float | None = None) -> None:
        self._cookies = [cookie for cookie in self._cookies if not cookie.is_expired(now)]

    def _replace(self, new_cookie: StoredCookie) -> None:
        kept = [
            cookie
            for cookie in self._cookies
            if not (
                cookie.domain == new_cookie.domain
                and cookie.path == new_cookie.path
                and cookie.name == new_cookie.name
            )
        ]
        self._cookies = kept

    def store(self, domain: str, host_only: bool, default_path: str, set_cookies: list[str]) -> None:
        """Применить заголовки Set-Cookie из ответа."""
        for header in set_cookies:
            cookie = parse_set_cookie(header, domain, host_only, default_path)
            self._replace(cookie)
            # Пустое значение и прошедший срок — удаление cookie (RFC 6265, 5.3).
            if cookie.value != "" and not cookie.is_expired():
                self._cookies.append(cookie)

    def cookies_for(self, host: str, path: str, secure: bool) -> list[StoredCookie]:
        self.purge_expired()
        matched = [
            cookie for cookie in self._cookies if cookie.matches(host, path, secure)
        ]
        # RFC 6265, 5.4: более специфичные пути идут первыми.
        matched.sort(key=lambda c: len(c.path), reverse=True)
        return matched

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise HttpError(f"Не удалось прочитать файл cookie {str(path)!r}: {exc}") from None

        parsed: list[StoredCookie] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 7:
                continue
            domain, _flag, cookie_path, secure, expires, name, value = fields[:7]
            http_only = len(fields) > 7 and fields[7].upper() == "TRUE"
            try:
                parsed.append(
                    StoredCookie(
                        domain=domain.lstrip(".").lower(),
                        host_only=_flag.upper() != "TRUE",
                        path=cookie_path or "/",
                        secure=secure.upper() == "TRUE",
                        expires=_normalize_expires(expires),
                        name=name,
                        value=value,
                        http_only=http_only,
                    )
                )
            except ValueError:
                continue
        self._cookies = parsed
        self.purge_expired()

    def save(self, path: Path) -> None:
        self.purge_expired()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(cookie.to_line() for cookie in self._cookies) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise HttpError(f"Не удалось сохранить файл cookie {str(path)!r}: {exc}") from None


def parse_set_cookie(
    header: str, domain: str, host_only: bool, default_path: str
) -> StoredCookie:
    """Разобрать одну строку Set-Cookie."""
    parts = header.split(";")
    name_value = parts[0]
    if "=" not in name_value:
        raise HttpError(f"Некорректный Set-Cookie: {header!r}")
    name, value = name_value.split("=", 1)

    cookie_domain = domain.strip().lower()
    cookie_path = default_path
    secure = False
    http_only = False
    expires: float | None = None
    max_age: float | None = None

    for raw_attr in parts[1:]:
        if "=" in raw_attr:
            attr_name, attr_value = raw_attr.split("=", 1)
        else:
            attr_name, attr_value = raw_attr, ""
        attr_name = attr_name.strip().lower()
        attr_value = attr_value.strip().strip('"')

        if attr_name == "domain" and attr_value:
            candidate = attr_value.lstrip(".").lower()
            base = domain.lower()
            if base == candidate or base.endswith("." + candidate):
                cookie_domain = candidate
                host_only = False
        elif attr_name == "path" and attr_value.startswith("/"):
            cookie_path = attr_value
        elif attr_name == "secure":
            secure = True
        elif attr_name == "httponly":
            http_only = True
        elif attr_name == "max-age":
            try:
                max_age = float(attr_value)
            except ValueError:
                continue
        elif attr_name == "expires":
            parsed = parse_http_date(attr_value)
            if parsed is not None:
                expires = parsed

    if max_age is not None:
        expires = None if max_age <= 0 else time.time() + max_age
        if max_age <= 0:
            value = ""

    return StoredCookie(
        domain=cookie_domain,
        host_only=host_only,
        path=cookie_path,
        secure=secure,
        expires=expires,
        name=name.strip(),
        value=value,
        http_only=http_only,
    )


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_http_date(value: str) -> float | None:
    """Разобрать дату в формате HTTP-date (RFC 9110, 5.6.7) в unix-время."""
    cleaned = value.replace(",", " ")
    tokens = cleaned.split()
    day = month = year = None
    clock = None
    found_month = False

    for token in tokens:
        lowered = token.lower()
        if clock is None and ":" in token:
            clock = token
            continue
        if not found_month and lowered[:3] in _MONTHS:
            month = _MONTHS[lowered[:3]]
            found_month = True
            continue
        if token.isdigit():
            number = int(token)
            if day is None and number <= 31:
                day = number
            elif year is None:
                year = number

    if day is None or month is None or year is None or clock is None:
        return None
    if year < 100:
        year += 2000 if year < 70 else 1900

    try:
        hour, minute, second = (int(part) for part in clock.split(":"))
        struct = (year, month, day, hour, minute, second, 0, 0, 0)
        return float(calendar.timegm(struct))
    except (ValueError, OverflowError):
        return None
