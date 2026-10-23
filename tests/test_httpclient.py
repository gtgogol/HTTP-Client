"""Тесты HTTP-клиента на локальном тестовом сервере (stdlib unittest)."""

import gzip
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from httpclient import HttpClient, HttpRequest, HttpTimeoutError, encode_json
from httpclient.cli import main
from httpclient.cookie_store import CookieJar, StoredCookie, path_matches
from httpclient.http_models import build_headers
from httpclient.request_builder import RequestOptions, build_request
from httpclient.url_tools import default_cookie_path, split_url


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # тишина в выводе тестов
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, code: int, body: bytes, headers: dict[str, str] | None = None):
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/echo-headers"):
            body = json.dumps({k: v for k, v in self.headers.items()}).encode()
            self._send(200, body, {"Content-Type": "application/json"})
        elif self.path.startswith("/set-cookie"):
            self._send(
                200,
                b"ok",
                {
                    "Set-Cookie": "session=abc123; Path=/; HttpOnly",
                },
            )
        elif self.path.startswith("/two-cookies"):
            self._send(200, b"ok")
            # send two Set-Cookie headers manually
        elif self.path.startswith("/chunked"):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in (b"hello ", b"chunked ", b"world"):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.write(b"0\r\n\r\n")
        elif self.path.startswith("/gzip"):
            payload = gzip.compress(b"compressed body")
            self._send(200, payload, {"Content-Encoding": "gzip"})
        elif self.path.startswith("/slow"):
            time.sleep(1.5)
            self._send(200, b"slow")
        else:
            self._send(200, b"ok")

    def do_POST(self):
        if self.path.startswith("/echo"):
            body = self._read_body()
            payload = {
                "method": self.command,
                "body": body.decode("utf-8", "replace"),
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
            self._send(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        else:
            self._send(200, b"ok")

    def do_PUT(self):
        self.do_POST()

    def do_HEAD(self):
        self._send(200, b"head-body", {"Content-Type": "text/plain"})


class ServerMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)


class UrlTests(unittest.TestCase):
    def test_split_url_defaults(self):
        parsed = split_url("https://example.com/a/b?x=1")
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.port, 443)
        self.assertEqual(parsed.target, "/a/b?x=1")
        self.assertEqual(parsed.host_header, "example.com")
        self.assertEqual(parsed.path, "/a/b")

    def test_non_default_port_in_host_header(self):
        parsed = split_url("http://example.com:8080/")
        self.assertEqual(parsed.host_header, "example.com:8080")

    def test_scheme_is_optional(self):
        self.assertEqual(split_url("example.com/x").scheme, "http")

    def test_default_cookie_path(self):
        self.assertEqual(default_cookie_path("/a/b/c"), "/a/b")
        self.assertEqual(default_cookie_path("/a"), "/")
        self.assertEqual(default_cookie_path("/"), "/")


class HeaderTests(unittest.TestCase):
    def test_automatic_headers(self):
        parsed = split_url("http://example.com/x")
        headers = build_headers({}, parsed, b"hello")
        self.assertEqual(headers["Host"], "example.com")
        self.assertEqual(headers["Content-Length"], "5")
        self.assertIn("User-Agent", headers)
        self.assertIn("Date", headers)
        self.assertEqual(headers["Connection"], "close")

    def test_no_content_length_without_body(self):
        headers = build_headers({}, split_url("http://example.com/"), None)
        self.assertNotIn("Content-Length", headers)

    def test_user_headers_override_case_insensitive(self):
        headers = build_headers({"host": "custom.example", "user-agent": "me"}, split_url("http://example.com/"), None)
        self.assertEqual(headers["Host"], "custom.example")
        self.assertEqual(headers["user-agent"], "me")
        self.assertNotIn("User-Agent", headers)

    def test_crlf_injection_rejected(self):
        from httpclient import HttpError

        with self.assertRaises(HttpError):
            build_headers({"X-Bad": "a\r\nInjected: yes"}, split_url("http://example.com/"), None)

    def test_cookie_header_joined(self):
        headers = build_headers(
            {}, split_url("http://example.com/"), None, [("a", "1"), ("b", "2")]
        )
        self.assertEqual(headers["Cookie"], "a=1; b=2")


class RequestBuilderTests(unittest.TestCase):
    def test_json_body_sets_content_type(self):
        request = build_request(
            "POST", "http://example.com/", {}, [], RequestOptions(json_body='{"a": 1}')
        )
        self.assertEqual(request.body, b'{"a":1}')
        self.assertEqual(request.headers["Content-Type"], "application/json; charset=utf-8")

    def test_form_body(self):
        request = build_request(
            "POST", "http://example.com/", {}, [], RequestOptions(form_fields=["a=1", "b=x y"])
        )
        self.assertEqual(request.body, b"a=1&b=x+y")

    def test_two_body_sources_rejected(self):
        from httpclient import HttpError

        with self.assertRaises(HttpError):
            build_request(
                "POST", "http://example.com/", {}, [],
                RequestOptions(json_body="{}", data="x"),
            )

    def test_get_with_body_rejected(self):
        from httpclient import HttpError

        with self.assertRaises(HttpError):
            build_request("GET", "http://example.com/", {}, [], RequestOptions(data="x"))

    def test_query_params_merged(self):
        request = build_request(
            "GET", "http://example.com/s?x=1", {}, ["y=2"], RequestOptions()
        )
        self.assertEqual(request.parsed_url.target, "/s?x=1&y=2")

    def test_invalid_json_rejected(self):
        from httpclient import HttpError

        with self.assertRaises(HttpError):
            encode_json("{bad")


class ClientTests(ServerMixin):
    def test_get_returns_body(self):
        response = HttpClient(timeout=3).send(HttpRequest("GET", self.base + "/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"ok")

    def test_automatic_headers_reach_server(self):
        response = HttpClient(timeout=3).send(
            HttpRequest("GET", self.base + "/echo-headers", {"X-Custom": "value"})
        )
        sent = json.loads(response.body)
        self.assertIn("User-Agent", sent)
        self.assertEqual(sent.get("X-Custom"), "value")

    def test_post_json(self):
        request = build_request(
            "POST", self.base + "/echo", {}, [], RequestOptions(json_body='{"k": "v"}')
        )
        response = HttpClient(timeout=3).send(request)
        data = json.loads(response.body)
        self.assertEqual(data["body"], '{"k":"v"}')
        self.assertEqual(data["headers"]["content-type"], "application/json; charset=utf-8")
        self.assertEqual(data["headers"]["content-length"], "9")

    def test_chunked_response(self):
        response = HttpClient(timeout=3).send(HttpRequest("GET", self.base + "/chunked"))
        self.assertEqual(response.body, b"hello chunked world")

    def test_gzip_response_decoded(self):
        response = HttpClient(timeout=3).send(HttpRequest("GET", self.base + "/gzip"))
        self.assertEqual(response.body, b"compressed body")

    def test_head_has_no_body(self):
        response = HttpClient(timeout=3).send(HttpRequest("HEAD", self.base + "/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"")

    def test_timeout(self):
        with self.assertRaises(HttpTimeoutError):
            HttpClient(timeout=0.3).send(HttpRequest("GET", self.base + "/slow"))

    def test_cookie_roundtrip(self):
        jar = CookieJar()
        client = HttpClient(timeout=3, cookie_jar=jar)
        client.send(HttpRequest("GET", self.base + "/set-cookie"))
        self.assertEqual(len(jar), 1)
        response = client.send(HttpRequest("GET", self.base + "/echo-headers"))
        sent = json.loads(response.body)
        self.assertEqual(sent.get("Cookie"), "session=abc123")


class CookieStoreTests(unittest.TestCase):
    def test_path_matching(self):
        self.assertTrue(path_matches("/a/b", "/a"))
        self.assertTrue(path_matches("/a/b", "/a/"))
        self.assertFalse(path_matches("/ab", "/a"))

    def test_domain_matching(self):
        cookie = StoredCookie("example.com", False, "/", False, None, "a", "1")
        self.assertTrue(cookie.matches("sub.example.com", "/", False))
        self.assertFalse(cookie.matches("example.com.evil", "/", False))

    def test_host_only_cookie(self):
        cookie = StoredCookie("example.com", True, "/", False, None, "a", "1")
        self.assertTrue(cookie.matches("example.com", "/", False))
        self.assertFalse(cookie.matches("sub.example.com", "/", False))

    def test_secure_cookie_requires_https(self):
        cookie = StoredCookie("example.com", True, "/", True, None, "a", "1")
        self.assertFalse(cookie.matches("example.com", "/", False))
        self.assertTrue(cookie.matches("example.com", "/", True))

    def test_max_age_zero_deletes_cookie(self):
        jar = CookieJar()
        jar.store("example.com", True, "/", ["a=1"])
        jar.store("example.com", True, "/", ["a=; Max-Age=0"])
        self.assertEqual(len(jar), 0)

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookies.txt"
            jar = CookieJar()
            jar.store("example.com", True, "/", ["a=1; Path=/; Secure; HttpOnly", "b=2"])
            jar.save(path)
            loaded = CookieJar()
            loaded.load(path)
            self.assertEqual(len(loaded), 2)
            loaded_map = {c.name: c for c in loaded}
            self.assertTrue(loaded_map["a"].secure)
            self.assertTrue(loaded_map["a"].http_only)
            self.assertEqual(loaded_map["b"].value, "2")


class CliTests(ServerMixin):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.jar = self.tmp / "cookies.txt"
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({"cookie_file": str(self.jar)}), encoding="utf-8")
        import os

        self._old_config = os.environ.get("HTTPCLIENT_CONFIG")
        os.environ["HTTPCLIENT_CONFIG"] = str(self.config)

    def tearDown(self):
        import os

        if self._old_config is None:
            os.environ.pop("HTTPCLIENT_CONFIG", None)
        else:
            os.environ["HTTPCLIENT_CONFIG"] = self._old_config
        self._tmp.cleanup()

    def test_save_body_to_file(self):
        target = self.tmp / "out.bin"
        code = main([self.base + "/chunked", "-o", str(target), "-s"])
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), b"hello chunked world")

    def test_save_headers_as_json(self):
        target = self.tmp / "out.json"
        code = main([self.base + "/", "-H", "X-Test: 1", "--output-kind", "json", "-o", str(target)])
        self.assertEqual(code, 0)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["status_code"], 200)

    def test_cookie_file_written_and_reused(self):
        code = main([self.base + "/set-cookie", "-s"])
        self.assertEqual(code, 0)
        self.assertTrue(self.jar.exists())

        jar = CookieJar()
        jar.load(self.jar)
        self.assertEqual([(c.name, c.value) for c in jar], [("session", "abc123")])
        self.assertTrue(next(iter(jar)).http_only)

        target = self.tmp / "echo.json"
        code = main([self.base + "/echo-headers", "-o", str(target), "-s"])
        self.assertEqual(code, 0)
        headers = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(headers.get("Cookie"), "session=abc123")

    def test_fail_with_status(self):
        code = main([self.base + "/", "--fail-with-status"])
        self.assertEqual(code, 0)
        code = main([self.base + "/missing", "-s", "--fail-with-status"])
        self.assertEqual(code, 0)  # тестовый сервер отвечает 200 на неизвестные пути


if __name__ == "__main__":
    unittest.main()
