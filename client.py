"""HTTP-клиент с CLI: python client.py <URL> [METHOD] [параметры]."""

import sys

from httpclient.cli import main

__version__ = "1.0.0"

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
