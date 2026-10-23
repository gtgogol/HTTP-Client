"""Пользовательский конфиг клиента (необязательный)."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import HttpError

CONFIG_ENV_VAR = "HTTPCLIENT_CONFIG"


def default_config_path() -> Path:
    """Файл конфига по умолчанию: %APPDATA%\\http-client на Windows, ~/.config - на остальных."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "http-client" / "config.json"


def _resolve_path(raw: str) -> Path:
    return Path(raw).expanduser()


@dataclass(frozen=True)
class ClientConfig:
    cookie_file: Path

    @classmethod
    def load(cls, path: str | None = None) -> "ClientConfig":
        """Прочитать конфиг; при отсутствии файла вернуть значения по умолчанию."""
        if path is None:
            path = os.environ.get(CONFIG_ENV_VAR) or None
        config_path = Path(path).expanduser() if path else default_config_path()
        if not config_path.exists():
            return cls(cookie_file=_resolve_path("~/.http-client-cookies.txt"))

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise HttpError(f"Не удалось прочитать конфиг {str(config_path)!r}: {exc}") from None
        except json.JSONDecodeError as exc:
            raise HttpError(f"Конфиг {str(config_path)!r} не является JSON: {exc}") from None

        if not isinstance(raw, dict):
            raise HttpError(f"Конфиг {str(config_path)!r} должен быть JSON-объектом")

        cookie_file = raw.get("cookie_file")
        if cookie_file is None:
            cookie_file = "~/.http-client-cookies.txt"
        elif not isinstance(cookie_file, str) or not cookie_file.strip():
            raise HttpError("Поле cookie_file в конфиге должно быть непустой строкой")

        return cls(cookie_file=_resolve_path(cookie_file.strip()))
