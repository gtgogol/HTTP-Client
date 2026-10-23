"""Командный интерфейс HTTP-клиента.

Примеры:
    http-client https://httpbin.org/get
    http-client POST https://httpbin.org/post --json '{"a": 1}'
    http-client -X PUT https://example.com/api -H "X-Token: abc" -d hello
    http-client https://example.com/file.zip -o download.zip
    http-client https://httpbin.org/cookies -b session=abc -c cookies.json
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .client import DEFAULT_TIMEOUT, HttpClient
from .config import CONFIG_ENV_VAR, ClientConfig
from .cookie_store import CookieJar
from .errors import HttpClientError
from .output_writer import save_body, save_headers, save_summary
from .request_builder import RequestOptions, build_request
from .response_view import print_body, print_cookies, print_timing
from .url_tools import split_url

DEFAULT_METHOD = "GET"


@dataclass
class CliOverrides:
    """Что из аргументов командной строки задано пользователем явно."""

    method: bool = False
    host: bool = False
    content_type: bool = False
    cookie: bool = False
OUTPUT_KIND_HELP = (
    "что сохранять: body (тело, по умолчанию), headers (статус и заголовки), "
    "json (статус, заголовки и тело в JSON)"
)


def parse_header(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise argparse.ArgumentTypeError(
            f"заголовок должен иметь вид 'Имя: значение', получено {raw!r}"
        )
    name, value = raw.split(":", 1)
    if not name.strip():
        raise argparse.ArgumentTypeError(f"пустое имя заголовка в {raw!r}")
    return name.strip(), value.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="http-client",
        description="HTTP/1.1-клиент из командной строки (стандартная библиотека Python).",
        epilog=(
            f"Таймаут по умолчанию — {DEFAULT_TIMEOUT} с. "
            f"Путь к конфигу с cookie-банкой можно задать через {CONFIG_ENV_VAR}."
        ),
    )
    parser.add_argument("url", help="URL запроса, например https://example.com/api")
    parser.add_argument(
        "method",
        nargs="?",
        default=None,
        help=f"метод запроса (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS...), "
        f"по умолчанию {DEFAULT_METHOD}",
    )
    parser.add_argument(
        "-X",
        "--request",
        dest="method_flag",
        metavar="METHOD",
        help="явно указать метод (имеет приоритет над позиционным аргументом)",
    )

    body_group = parser.add_argument_group("тело запроса")
    body_group.add_argument(
        "--json",
        dest="json_body",
        metavar="JSON",
        help="тело в JSON (Content-Type: application/json)",
    )
    body_group.add_argument(
        "--form",
        dest="form_fields",
        action="append",
        default=[],
        metavar="КЛЮЧ=ЗНАЧЕНИЕ",
        help="поле формы (Content-Type: application/x-www-form-urlencoded), "
        "можно указывать несколько раз",
    )
    body_group.add_argument(
        "-d", "--data", metavar="TEXT", help="тело запроса простым текстом"
    )
    body_group.add_argument(
        "--data-file",
        metavar="PATH",
        help="отправить содержимое файла как тело (Content-Type: application/octet-stream)",
    )
    body_group.add_argument(
        "-T",
        "--body-encoding",
        default="utf-8",
        metavar="ENC",
        help="кодировка для --data (по умолчанию utf-8)",
    )

    request_group = parser.add_argument_group("параметры запроса")
    request_group.add_argument(
        "-H",
        "--header",
        dest="headers",
        action="append",
        type=parse_header,
        default=[],
        metavar="'ИМЯ: ЗНАЧЕНИЕ'",
        help="кастомный заголовок; можно указывать несколько раз",
    )
    request_group.add_argument(
        "-p",
        "--param",
        dest="params",
        action="append",
        default=[],
        metavar="КЛЮЧ=ЗНАЧЕНИЕ",
        help="query-параметр URL; можно указывать несколько раз",
    )
    request_group.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="СЕК",
        help=f"таймаут ожидания ответа в секундах (по умолчанию {DEFAULT_TIMEOUT})",
    )

    cookie_group = parser.add_argument_group("cookie")
    cookie_group.add_argument(
        "-b",
        "--cookie",
        dest="cookies",
        action="append",
        default=[],
        metavar="ИМЯ=ЗНАЧЕНИЕ",
        help="отправить cookie, помимо сохранённых; можно указывать несколько раз",
    )
    cookie_group.add_argument(
        "-c",
        "--cookie-jar",
        metavar="PATH",
        help="файл cookie-банки (по умолчанию из конфига, ~/.http-client-cookies.txt)",
    )
    cookie_group.add_argument(
        "-B",
        "--no-cookies",
        action="store_true",
        help="не использовать и не сохранять cookie-банку",
    )
    cookie_group.add_argument(
        "--list-cookies",
        action="store_true",
        help="показать cookie из банки и выйти",
    )
    cookie_group.add_argument(
        "--clear-cookies",
        action="store_true",
        help="очистить cookie-банку и выйти",
    )

    output_group = parser.add_argument_group("сохранение ответа")
    output_group.add_argument(
        "-o", "--output", metavar="PATH", help="сохранить ответ в файл"
    )
    output_group.add_argument(
        "--output-kind",
        choices=("body", "headers", "json"),
        default="body",
        help=OUTPUT_KIND_HELP,
    )
    output_group.add_argument(
        "--append",
        action="store_true",
        help="дописывать в файл вместо перезаписи",
    )
    output_group.add_argument(
        "-s",
        "--silent",
        action="store_true",
        help="не печатать ответ в консоль",
    )
    output_group.add_argument(
        "-i",
        "--include-headers",
        action="store_true",
        help="печатать статус и заголовки ответа",
    )
    output_group.add_argument(
        "--fail-with-status",
        action="store_true",
        help="вернуть код 1, если статус ответа >= 400",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def resolve_method(args: argparse.Namespace) -> str:
    if args.method_flag:
        return args.method_flag
    if args.method:
        return args.method
    return DEFAULT_METHOD


def collect_headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in pairs:
        headers[name] = value
    return headers


def add_cli_cookies(jar: CookieJar, raw_cookies: list[str], domain: str) -> None:
    """Cookies из -b действуют только на host запроса."""
    for raw in raw_cookies:
        if "=" not in raw:
            raise HttpClientError(
                f"Cookie должна иметь вид ИМЯ=ЗНАЧЕНИЕ, получено {raw!r}"
            )
        name, value = raw.split("=", 1)
        if not name.strip():
            raise HttpClientError(f"Пустое имя cookie в {raw!r}")
        if any(ch in value for ch in "\r\n;"):
            raise HttpClientError(f"Недопустимое значение cookie {name.strip()!r}")
        jar.store(
            domain=domain,
            host_only=True,
            default_path="/",
            set_cookies=[f"{name.strip()}={value}"],
        )


def _save(response, args: argparse.Namespace) -> None:
    if args.output_kind == "headers":
        save_headers(response, args.output, append=args.append)
    elif args.output_kind == "json":
        save_summary(response, args.output)
    else:
        save_body(response, args.output, append=args.append)


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout должен быть положительным числом")

    config = ClientConfig.load()
    method = resolve_method(args)
    parsed_url = split_url(args.url)

    cookie_path = (
        Path(args.cookie_jar).expanduser() if args.cookie_jar else config.cookie_file
    )
    jar = CookieJar()
    jar.load(cookie_path)

    if args.list_cookies:
        print_cookies(jar)
        return 0
    if args.clear_cookies:
        jar.clear()
        jar.save(cookie_path)
        print(f"Cookie-банка очищена: {cookie_path}")
        return 0

    use_cookies = not args.no_cookies
    if use_cookies:
        add_cli_cookies(jar, args.cookies, parsed_url.domain)

    request = build_request(
        method=method,
        url=args.url,
        headers=collect_headers(args.headers),
        params=args.params,
        options=RequestOptions(
            json_body=args.json_body,
            form_fields=args.form_fields,
            data=args.data,
            file_path=args.data_file,
            encoding=args.body_encoding,
        ),
    )

    client = HttpClient(timeout=args.timeout, cookie_jar=jar if use_cookies else None)

    started = time.perf_counter()
    response = client.send(request)
    elapsed = time.perf_counter() - started

    if use_cookies:
        jar.save(cookie_path)

    if args.output:
        _save(response, args)
        status_line = f"HTTP {response.status_code} {response.reason}".rstrip()
        if args.include_headers:
            print(f"{status_line} — тело/данные сохранены в {args.output}")
        elif not args.silent:
            print(f"{status_line} — данные сохранены в {args.output}")
    elif not args.silent:
        print_body(response, show_headers=args.include_headers)
        print_timing(elapsed, len(response.body))

    return 1 if args.fail_with_status and response.status_code >= 400 else 0


def configure_console() -> None:
    """Переключить stdout/stderr на UTF-8, чтобы не зависеть от кодовой страницы."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    """Точка входа: ошибки клиента показываются без трейсбека."""
    configure_console()
    try:
        return run(argv)
    except HttpClientError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nПрервано пользователем", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
