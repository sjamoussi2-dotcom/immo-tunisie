# -*- coding: utf-8 -*-
"""
Tiny dependency-free web micro-framework (stdlib only, uses Jinja2 for templates).
Provides just enough of Flask's API surface (routing, request/response,
sessions, flash messages, render_template, url_for) for this project.
"""
import cgi
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import time
from http import cookies as http_cookies
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIServer
from socketserver import ThreadingMixIn

from jinja2 import Environment, FileSystemLoader, select_autoescape

SECRET_KEY = os.environ.get("IMMO_SECRET_KEY", "dev-secret-key-change-in-production").encode("utf-8")


# ---------------------------------------------------------------------------
# MultiDict
# ---------------------------------------------------------------------------
class MultiDict(dict):
    def get(self, key, default=None):
        v = dict.get(self, key)
        if v is None:
            return default
        if isinstance(v, list):
            return v[0] if v else default
        return v

    def getlist(self, key):
        v = dict.get(self, key, [])
        return v if isinstance(v, list) else [v]


class FileStorage:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data  # bytes, read eagerly (cgi.FieldStorage closes
                            # its temp files once the parent object is
                            # garbage-collected, so we can't defer reading)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._data)


# ---------------------------------------------------------------------------
# Session (signed cookie, stateless like Flask's default)
# ---------------------------------------------------------------------------
def _sign(payload_b64):
    sig = hmac.new(SECRET_KEY, payload_b64, hashlib.sha256).hexdigest()
    return sig


def encode_session(data):
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    b64 = raw.hex().encode("ascii")
    sig = _sign(b64)
    return (b64.decode("ascii") + "." + sig)


def decode_session(token):
    try:
        b64_str, sig = token.rsplit(".", 1)
        b64 = b64_str.encode("ascii")
        expected = _sign(b64)
        if not hmac.compare_digest(sig, expected):
            return {}
        raw = bytes.fromhex(b64_str)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------
class HTTPError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message or f"Error {code}"


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "/")
        self.referrer = environ.get("HTTP_REFERER")

        qs = environ.get("QUERY_STRING", "")
        self.args = MultiDict(parse_qs(qs, keep_blank_values=True))

        self.form = MultiDict()
        self.files = {}
        self._parse_body()

        cookie_header = environ.get("HTTP_COOKIE", "")
        jar = http_cookies.SimpleCookie()
        jar.load(cookie_header)
        self.cookies = {k: v.value for k, v in jar.items()}

        token = self.cookies.get("session")
        self.session = decode_session(token) if token else {}
        self.user = None  # set by app after loading

    def _parse_body(self):
        if self.method not in ("POST", "PUT", "PATCH"):
            return
        content_type = self.environ.get("CONTENT_TYPE", "")
        try:
            length = int(self.environ.get("CONTENT_LENGTH", 0) or 0)
        except ValueError:
            length = 0

        if content_type.startswith("multipart/form-data"):
            fs = cgi.FieldStorage(
                fp=self.environ["wsgi.input"],
                environ=self.environ,
                keep_blank_values=True,
            )
            if fs.list:
                for item in fs.list:
                    if item.filename:
                        self.files.setdefault(item.name, []).append(
                            FileStorage(item.filename, item.file.read())
                        )
                    else:
                        self.form.setdefault(item.name, []).append(item.value)
        else:
            body = self.environ["wsgi.input"].read(length) if length else b""
            parsed = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
            for k, v in parsed.items():
                self.form[k] = v


class Response:
    def __init__(self, body="", status=200, headers=None, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.status = status
        self.headers = headers or []
        self.headers.append(("Content-Type", content_type))

    def set_cookie(self, key, value, path="/", httponly=True):
        cookie = f"{key}={value}; Path={path}"
        if httponly:
            cookie += "; HttpOnly"
        self.headers.append(("Set-Cookie", cookie))


def redirect(location, code=302):
    return Response("", status=code, headers=[("Location", location)])


STATUS_TEXT = {
    200: "OK", 302: "Found", 400: "Bad Request", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error",
}


def abort(code, message=None):
    raise HTTPError(code, message)


# ---------------------------------------------------------------------------
# Routing / App
# ---------------------------------------------------------------------------
_CONVERTER_RE = re.compile(r"<(?:(?P<conv>[a-zA-Z_]+):)?(?P<name>[a-zA-Z_]\w*)>")


def _compile_path(path):
    def repl(m):
        conv = m.group("conv") or "string"
        name = m.group("name")
        if conv == "int":
            return f"(?P<{name}>\\d+)"
        return f"(?P<{name}>[^/]+)"

    pattern = "^" + _CONVERTER_RE.sub(repl, path) + "$"
    converters = {}
    for m in _CONVERTER_RE.finditer(path):
        converters[m.group("name")] = m.group("conv") or "string"
    return re.compile(pattern), converters


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class App:
    def __init__(self, template_dir, static_dir, static_url="/static/"):
        self.routes = []  # (methods, regex, converters, handler, endpoint, raw_path)
        self.endpoint_paths = {}
        self.static_dir = static_dir
        self.static_url = static_url
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html"]),
        )
        self.context_providers = []

    def context_processor(self, fn):
        self.context_providers.append(fn)
        return fn

    def route(self, path, endpoint, methods=("GET",)):
        def decorator(fn):
            regex, converters = _compile_path(path)
            self.routes.append((set(methods), regex, converters, fn, endpoint, path))
            self.endpoint_paths[endpoint] = path
            return fn
        return decorator

    def url_for(self, endpoint, **values):
        if endpoint == "static":
            filename = values.pop("filename", "")
            url = self.static_url + filename
        else:
            path = self.endpoint_paths[endpoint]

            def repl(m):
                name = m.group("name")
                return str(values.pop(name))

            url = _CONVERTER_RE.sub(repl, path)
        if values:
            url += "?" + urlencode(values, doseq=True)
        return url

    def render_template(self, request, name, **context):
        base_ctx = {
            "url_for": self.url_for,
            "current_user": request.user,
            "get_flashed_messages": lambda with_categories=False: (
                [tuple(x) for x in request.session.pop("_flashes", [])]
                if with_categories else
                [x[1] for x in request.session.pop("_flashes", [])]
            ),
        }
        for provider in self.context_providers:
            base_ctx.update(provider(request))
        base_ctx.update(context)
        template = self.env.get_template(name)
        return Response(template.render(**base_ctx))

    def flash(self, request, message, category="message"):
        request.session.setdefault("_flashes", []).append([category, message])

    def _serve_static(self, path):
        rel = path[len(self.static_url):]
        full = os.path.normpath(os.path.join(self.static_dir, rel))
        if not full.startswith(os.path.normpath(self.static_dir)) or not os.path.isfile(full):
            raise HTTPError(404)
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            data = f.read()
        return Response(data, content_type=ctype or "application/octet-stream")

    def dispatch(self, request):
        if request.path.startswith(self.static_url):
            return self._serve_static(request.path)

        allowed_methods = set()
        for methods, regex, converters, fn, endpoint, raw_path in self.routes:
            m = regex.match(request.path)
            if not m:
                continue
            allowed_methods |= methods
            if request.method not in methods:
                continue
            kwargs = {}
            for name, value in m.groupdict().items():
                if converters.get(name) == "int":
                    value = int(value)
                kwargs[name] = value
            return fn(request, **kwargs)

        if allowed_methods:
            raise HTTPError(405)
        raise HTTPError(404)

    def wsgi_app(self, environ, start_response):
        request = Request(environ)
        before_user_hook(self, request)
        try:
            result = self.dispatch(request)
            if not isinstance(result, Response):
                result = Response(result)
        except HTTPError as e:
            status_text = STATUS_TEXT.get(e.code, "Error")
            result = Response(
                f"<h1>{e.code} {status_text}</h1><p>{e.message or ''}</p>",
                status=e.code,
            )

        token = encode_session(request.session)
        result.set_cookie("session", token)

        status_line = f"{result.status} {STATUS_TEXT.get(result.status, 'OK')}"
        headers = result.headers + [("Content-Length", str(len(result.body)))]
        start_response(status_line, headers)
        return [result.body]

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

    def run(self, host="0.0.0.0", port=5000):
        with make_server(host, port, self, server_class=ThreadingWSGIServer) as httpd:
            print(f"Serving on http://{host}:{port}")
            httpd.serve_forever()


# Hook point set by app.py to attach request.user before dispatch
def before_user_hook(app, request):
    pass

