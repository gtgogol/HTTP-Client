"""Построение HttpRequest из параметров командной строки."""

from dataclasses import dataclass, field

from .body import PreparedBody, encode_form, encode_json, encode_text, read_body_file
from .errors import HttpError
from .http_models import HttpRequest
from .url_tools import merge_query

# Методы, у которых тело по спецификации не имеет определённой семантики.
NO_BODY_METHODS = ("GET", "HEAD", "TRACE", "CONNECT")


@dataclass
class RequestOptions:
    """Сырые параметры тела запроса, ещё без кодирования."""

    json_body: str | None = None
    form_fields: list[str] = field(default_factory=list)
    data: str | None = None
    file_path: str | None = None
    encoding: str = "utf-8"

    @property
    def is_empty(self) -> bool:
        return (
            self.json_body is None
            and not self.form_fields
            and self.data is None
            and self.file_path is None
        )

    @property
    def count(self) -> int:
        return (
            sum(
                value is not None
                for value in (self.json_body, self.data, self.file_path)
            )
            + bool(self.form_fields)
        )


def prepare_body(options: RequestOptions) -> PreparedBody | None:
    if options.is_empty:
        return None
    if options.count > 1:
        raise HttpError(
            "Тело можно задать только одним способом: --json, --form, --data или --data-file"
        )
    if options.json_body is not None:
        return encode_json(options.json_body)
    if options.form_fields:
        return encode_form(options.form_fields)
    if options.data is not None:
        return encode_text(options.data, options.encoding)
    return read_body_file(options.file_path or "")


def build_request(
    method: str,
    url: str,
    headers: dict[str, str],
    params: list[str],
    options: RequestOptions,
    default_content_type: str | None = None,
) -> HttpRequest:
    """Собрать HttpRequest: URL + query, тело, Content-Type, пользовательские заголовки."""
    method_upper = method.strip().upper()
    body = prepare_body(options)

    if body is not None and method_upper in NO_BODY_METHODS:
        raise HttpError(
            f"Метод {method_upper} по RFC 9110 не передаёт тело; "
            "используйте POST/PUT/PATCH или флаг -X с методом, допускающим тело"
        )

    request_headers = dict(headers)
    if body is not None:
        content_type = body.content_type or default_content_type
        supplied = {name.lower() for name in request_headers}
        if content_type and "content-type" not in supplied:
            request_headers["Content-Type"] = content_type

    return HttpRequest(
        method=method_upper,
        url=merge_query(url, params),
        headers=request_headers,
        body=body.data if body is not None else None,
    )

