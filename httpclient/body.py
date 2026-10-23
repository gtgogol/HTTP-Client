"""Подготовка тела запроса: текст, JSON, form-urlencoded или файл."""

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from .errors import HttpError

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=utf-8"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
BINARY_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class PreparedBody:
    """Байты тела и Content-Type, который нужно выставить автоматически."""

    data: bytes
    content_type: str | None = None


def encode_text(text: str, encoding: str = "utf-8") -> PreparedBody:
    try:
        data = text.encode(encoding)
    except LookupError:
        raise HttpError(f"Неизвестная кодировка тела: {encoding!r}") from None
    except UnicodeEncodeError as exc:
        raise HttpError(f"Тело нельзя закодировать в {encoding}: {exc}") from None
    return PreparedBody(data)


def encode_json(text: str) -> PreparedBody:
    """Проверить JSON и сжать его без лишних пробелов."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"Тело не является корректным JSON: {exc}") from None
    payload = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return PreparedBody(payload.encode("utf-8"), JSON_CONTENT_TYPE)


def encode_form(pairs: list[str]) -> PreparedBody:
    """Превратить список "ключ=значение" в application/x-www-form-urlencoded."""
    items: list[tuple[str, str]] = []
    for raw in pairs:
        if "=" not in raw:
            raise HttpError(f"Ожидался формат ключ=значение, получено: {raw!r}")
        key, value = raw.split("=", 1)
        if not key.strip():
            raise HttpError(f"Пустой ключ в поле формы: {raw!r}")
        items.append((key.strip(), value))
    data = urlencode(items, doseq=False, encoding="utf-8").encode("ascii")
    return PreparedBody(data, FORM_CONTENT_TYPE)


def read_body_file(path: str) -> PreparedBody:
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise HttpError(f"Не удалось прочитать файл тела {path!r}: {exc}") from None
    return PreparedBody(data, BINARY_CONTENT_TYPE)
