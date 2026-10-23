"""Запуск CLI без установки пакета: python httpclient/cli.py ..."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Прямой запуск файла: добавляем папку проекта, чтобы импортировать пакет.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpclient.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
