"""Сохранение ответа (тело, заголовки или JSON-сводка) в файл."""

import json
from pathlib import Path

from .errors import HttpError
from .http_models import HttpResponse


def flatten_headers(response: HttpResponse) -> dict[str, str]:
    """Заголовки ответа в виде словаря; повторяющиеся склеиваются."""
    result: dict[str, str] = {}
    for name, value in response.headers.items():
        result[name] = "; ".join(value) if isinstance(value, list) else str(value)
    return result


def save_body(response: HttpResponse, path: str, append: bool = False) -> None:
    """Записать тело ответа в файл (в байтах, без изменения кодировки)."""
    mode = "ab" if append else "wb"
    try:
        with open(path, mode) as file:
            file.write(response.body)
    except OSError as exc:
        raise HttpError(f"Не удалось сохранить тело в {path!r}: {exc}") from None


def save_text(text: str, path: str, encoding: str = "utf-8", append: bool = False) -> None:
    mode = "a" if append else "w"
    try:
        with open(path, mode, encoding=encoding, newline="") as file:
            file.write(text)
    except OSError as exc:
        raise HttpError(f"Не удалось сохранить данные в {path!r}: {exc}") from None


def save_headers(response: HttpResponse, path: str, append: bool = False) -> None:
    """Записать статус и заголовки ответа в текстовом виде."""
    lines = [f"HTTP/1.1 {response.status_code} {response.reason}".rstrip()]
    for name, value in response.headers.items():
        values = value if isinstance(value, list) else [value]
        for single in values:
            lines.append(f"{name}: {single}")
    save_text("\n".join(lines) + "\n", path, append=append)


def save_summary(response: HttpResponse, path: str) -> None:
    """Сохранить JSON: статус, заголовки, размер тела и тело как текст (если оно текстовое)."""
    summary: dict[str, object] = {
        "status_code": response.status_code,
        "reason": response.reason,
        "headers": flatten_headers(response),
        "body_size": len(response.body),
        "body_sha256": __import__("hashlib").sha256(response.body).hexdigest(),
    }
    try:
        summary["body"] = response.text
    except UnicodeDecodeError:
        pass
    save_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", path
    )


def suggest_filename(response: HttpResponse, url: str) -> str:
    """Имя файла из Content-Disposition, иначе из пути URL."""
    from urllib.parse import unquote, urlsplit

    disposition = response.header("content-disposition") or ""
    for part in disposition.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "filename" and value.strip():
            name = Path(unquote(value.strip().strip('"'))).name
            if name:
                return name

    path = urlsplit(url).path
    name = Path(unquote(path)).name
    return name or "response.bin"
