# HTTP-Client

Учебный HTTP/1.1-клиент из командной строки, написанный на стандартной
библиотеке Python (без сторонних зависимостей).

## Возможности

- внутренняя реализация HTTP/1.1 поверх `socket`;
- указание URL и метода запроса (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, ...);
- тело запроса: JSON, `application/x-www-form-urlencoded`, произвольный текст или файл;
- автоматические заголовки: `Host`, `User-Agent`, `Date`, `Accept`, `Accept-Encoding`, `Connection`, `Content-Length`, `Content-Type`;
- кастомные заголовки с проверкой на подмену строк (CRLF-инъекции);
- таймаут ожидания ответа;
- сохранение ответа в файл: тело, заголовки или JSON-сводка;
- cookie: встроенная cookie-банка с сохранением в файл и поддержкой
  `Domain`, `Path`, `Secure`, `HttpOnly`, `Expires`, `Max-Age`;
- разбор ответов: `1xx`, `chunked`, `Content-Length`, тело до закрытия соединения,
  несколько заголовков с одинаковым именем, распаковка `gzip`/`deflate`.

## Структура проекта

```
client.py                     точка входа CLI (python client.py ...)
run_http_client.py            точка входа без установки пакета
httpclient/
  cli.py                      разбор аргументов и сценарий выполнения
  client.py                   транспорт: соединение, отправка, приём, cookie
  http_models.py              HttpRequest / HttpResponse, автоматические заголовки
  response_parser.py          чтение статуса, заголовков и тела из сокета
  request_builder.py          каркас сборки запроса из параметров CLI
  body.py                     подготовка тела: JSON, form, текст, файл
  cookie_store.py             cookie-банка и разбор Set-Cookie
  url_tools.py                разбор URL, query-параметры, путь для cookie
  output_writer.py            сохранение тела / заголовков / JSON-сводки в файл
  response_view.py            вывод ответа в консоль
  config.py                   пути файлов (cookie-банка по умолчанию)
  user_agent.py               значение User-Agent
  errors.py / exceptions.py   классы ошибок
  __main__.py                 запуск как python -m httpclient
tests/test_httpclient.py      тесты на локальном сервере (unittest)
requirements.txt, pyproject.toml
```

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
```

Запуск:

```bash
python client.py <URL> [METHOD] [параметры]
python -m httpclient <URL> [METHOD] [параметры]
python run_http_client.py <URL> [METHOD] [параметры]
```

Файл `httpclient/cli.py` запускать напрямую (`python httpclient/cli.py`) не нужно:
внутри пакета используются относительные импорты. Вместо этого есть
`run_http_client.py`.

Можно установить пакет и получить команду `http-client`:

```bash
pip install -e .
http-client https://httpbin.org/get
```

## Примеры

```bash
# GET
python client.py https://httpbin.org/get

# метод можно указать позиционно или через -X
python client.py POST https://httpbin.org/post --json '{"name": "test"}'
python client.py -X DELETE https://httpbin.org/delete

# формы и текстовое тело
python client.py POST https://httpbin.org/post --form user=ivan --form role=admin
python client.py PUT https://httpbin.org/put -d "hello world"

# тело из файла и кастомные заголовки
python client.py POST https://httpbin.org/post --data-file payload.bin -H "X-Token: abc"

# query-параметры, таймаут и показ заголовков
python client.py https://httpbin.org/get -p q=http-client -p page=1 -t 5 -i

# сохранить ответ в файл
python client.py https://example.com/file.zip -o download.zip
python client.py https://httpbin.org/get -o response.json --output-kind json
python client.py https://httpbin.org/get -o headers.txt --output-kind headers

# cookie: отправятся в запросе и сохранятся в файле банки
python client.py https://httpbin.org/cookies/set?session=abc
python client.py https://httpbin.org/cookies -c cookies.txt
python client.py https://httpbin.org/cookies -b foo=bar
python client.py --list-cookies
```

Код возврата: `0` — успех, `1` — статус ответа `>= 400` (с флагом
`--fail-with-status`), `2` — ошибка клиента (сеть, таймаут, некорректный URL).

## Автоматические заголовки

| Заголовок         | Когда выставляется                                           |
|-------------------|--------------------------------------------------------------|
| `Host`            | всегда (RFC 9112, 3.2), из URL с портом, если он нестандартный |
| `User-Agent`      | всегда                                                       |
| `Date`            | всегда, в формате IMF-fixdate (RFC 9110, 5.6.7)              |
| `Accept`          | `*/*`                                                        |
| `Accept-Encoding` | `identity`                                                   |
| `Connection`      | `close`                                                      |
| `Content-Length`  | при наличии тела                                             |
| `Content-Type`    | по способу тела (`--json`, `--form`, `--data-file`)          |
| `Cookie`          | из cookie-банки и параметра `-b`, если `Cookie` не задан вручную |

Пользовательские заголовки (`-H`) имеют приоритет над автоматическими;
сравнение имён регистронезависимое.

## Cookie

Cookie-банка по умолчанию хранится в файле
`%APPDATA%\http-client\config.json` → ключ `cookie_file`
(на Linux/macOS — `~/.config/http-client/config.json`), по умолчанию
`~/.http-client-cookies.txt`. Путь к конфигу можно переопределить
переменной окружения `HTTPCLIENT_CONFIG`, а к самому файлу банки — флагом `-c`.

Формат файла совместим с `Netscape cookies.txt` (как у `curl`).
Атрибуты `Secure`, `HttpOnly` и признак host-only сохраняются в файле.

## Программное использование

```python
from httpclient import HttpClient, HttpRequest
from httpclient.cookie_store import CookieJar

response = HttpClient(timeout=5).send(HttpRequest("GET", "https://httpbin.org/get"))
print(response.status_code, response.header("content-type"))
print(response.text)

jar = CookieJar()
client = HttpClient(timeout=5, cookie_jar=jar)
client.send(HttpRequest("GET", "https://httpbin.org/cookies/set?token=1"))
```

## Тесты

```bash
python -m unittest discover -s tests -v
```

Тесты поднимают локальный HTTP-сервер на `127.0.0.1` и проверяют заголовки,
таймаут, `chunked`, `gzip`, cookie, сохранение в файл и работу CLI.
