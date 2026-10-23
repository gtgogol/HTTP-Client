"""Вывод ответа в консоль."""

import sys

from .http_models import HttpResponse
from .output_writer import flatten_headers

MAX_PREVIEW_BYTES = 8192
_CONTROL_BYTES = {9, 10, 13}


def colorize(text: str, code: str) -> str:
    if not output_is_tty():
        return text
    return f"\033[{code}m{text}\033[0m"


def output_is_tty() -> bool:
    stream = sys.stdout
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def status_color(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "32"  # зелёный
    if 300 <= status_code < 400:
        return "36"  # голубой
    if 400 <= status_code < 500:
        return "33"  # жёлтый
    return "31"  # красный


def print_headers(response: HttpResponse) -> None:
    line = f"HTTP {response.status_code} {response.reason}".rstrip()
    print(colorize(line, status_color(response.status_code)))
    for name, value in flatten_headers(response).items():
        print(f"{name}: {value}")
    print()


def _looks_textual(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    return all(byte >= 32 or byte in _CONTROL_BYTES for byte in data)


def print_body(response: HttpResponse, show_headers: bool = True) -> None:
    if show_headers:
        print_headers(response)

    body = response.body
    if not body:
        print("(пустое тело ответа)")
        return

    if not _looks_textual(body):
        print(f"[бинарные данные, {len(body)} байт]")
        return

    preview = body[:MAX_PREVIEW_BYTES]
    try:
        text = preview.decode("utf-8")
    except UnicodeDecodeError:
        if len(preview) < len(body):
            # Обрезали посреди многобайтового символа — дочитываем символ.
            text = body[:MAX_PREVIEW_BYTES + 3].decode("utf-8", errors="ignore")
        else:
            text = body.decode("latin1", errors="replace")

    print(text, end="" if text.endswith("\n") else "\n")
    if len(body) > len(preview):
        print(f"[показаны первые {MAX_PREVIEW_BYTES} из {len(body)} байт; используйте -o для сохранения]")


def print_cookies(cookies) -> None:
    cookies = list(cookies)
    if not cookies:
        print("Cookie-банка пуста")
        return
    print(f"Cookie ({len(cookies)}):")
    for cookie in cookies:
        flags = []
        if cookie.secure:
            flags.append("secure")
        if cookie.http_only:
            flags.append("httponly")
        if cookie.host_only:
            flags.append("host-only")
        if cookie.expires is not None:
            flags.append("expires")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {cookie.domain}\t{cookie.path}\t{cookie.name}={cookie.value}{suffix}")


def print_timing(elapsed: float, body_size: int) -> None:
    print(
        colorize(f"[{elapsed * 1000:.0f} мс, тело: {body_size} байт]", "2"),
        file=sys.stderr,
    )
