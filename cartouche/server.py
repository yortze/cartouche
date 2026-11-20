"""Serveur HTTP local (127.0.0.1 seulement) qui sert l'interface et l'API."""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .core import ActionError, Core

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
MAX_BODY = 1 << 20


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, core: Core):
        super().__init__(address, Handler)
        self.core = core
        self.token = secrets.token_urlsafe(24)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


class Handler(BaseHTTPRequestHandler):
    server: AppServer
    server_version = "Cartouche"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # journal discret
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ réponses

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, status: int, message: str, fix: str | None = None) -> None:
        self._json(status, {"error": message, "fix": fix})

    # ------------------------------------------------------------------ contrôles

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in self.server.allowed_hosts}

    def _token_ok(self, body: dict | None = None) -> bool:
        token = self.headers.get("X-Cartouche-Token") or (body or {}).get("token") or ""
        return hmac.compare_digest(str(token), self.server.token)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("requête trop grosse")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("objet JSON attendu")
        return data

    # ------------------------------------------------------------------ GET

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "Hôte refusé")
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            return self._index()
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith("/img/"):
            return self._image(path[len("/img/"):])
        if path == "/api/hello":
            return self._json(200, {"app": "cartouche", "version": __version__})
        if not path.startswith("/api/"):
            return self._error(404, "Introuvable")
        if not self._token_ok():
            return self._error(403, "Jeton manquant ou invalide")
        core = self.server.core
        core.ping()  # toute requête de l'interface prouve que la fenêtre est ouverte
        routes = {
            "/api/status": lambda: core.status(),
            "/api/library": lambda: self._library(query),
            "/api/settings": lambda: core.get_settings(),
            "/api/emu-settings": lambda: core.emu_settings(),
            "/api/diagnostics": lambda: core.diagnostics(),
            "/api/updates": lambda: core.updates(force=query.get("force") == ["1"]),
        }
        self._dispatch(routes.get(path))

    def _library(self, query: dict):
        if query.get("refresh") == ["1"]:
            self.server.core.invalidate()
        return self.server.core.library()

    def _index(self) -> None:
        try:
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self._error(500, "Interface introuvable")
        html = html.replace("{{TOKEN}}", self.server.token).replace("{{VERSION}}", __version__)
        self._send(200, html.encode("utf-8"), TYPES[".html"],
                   {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})

    def _static(self, relative: str) -> None:
        relative = urllib.parse.unquote(relative)
        target = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "Introuvable")
        content_type = TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type, {"Cache-Control": "no-cache"})

    def _image(self, key: str) -> None:
        data = self.server.core.images.read(key)
        if data is None:
            return self._error(404, "Image introuvable")
        self._send(200, data, "image/png", {"Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------------------------ POST

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._error(403, "Origine refusée")
        try:
            body = self._read_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._error(400, f"Requête invalide : {exc}")
        path = urllib.parse.urlsplit(self.path).path
        if not self._token_ok(body if path == "/api/bye" else None):
            return self._error(403, "Jeton manquant ou invalide")
        core = self.server.core
        if path != "/api/bye":
            core.ping()
        routes = {
            "/api/ping": lambda: (core.ping(), {"ok": True})[1],
            "/api/bye": lambda: (core.bye(), {"ok": True})[1],
            "/api/quit": lambda: (core.shutdown_event.set(), {"ok": True})[1],
            "/api/install": lambda: core.install(str(body.get("id", "")), bool(body.get("confirm"))),
            "/api/jobs/cancel": lambda: {"ok": core.cancel_job(str(body.get("id", "")))},
            "/api/launch": lambda: core.launch(str(body.get("key", ""))),
            "/api/stop": lambda: {"ok": core.stop()},
            "/api/open": lambda: (core.open_location(str(body.get("kind", "")), str(body.get("ref", ""))),
                                  {"ok": True})[1],
            "/api/open-url": lambda: (core.open_url(str(body.get("url", ""))), {"ok": True})[1],
            "/api/pick": lambda: core.pick(str(body.get("kind", ""))),
            "/api/settings": lambda: core.set_settings(dict(body.get("changes") or {})),
            "/api/sources/remove": lambda: core.remove_source(str(body.get("kind", "")), str(body.get("path", ""))),
            "/api/emu-settings": lambda: core.set_emu_settings(dict(body.get("changes") or {})),
            "/api/emu-settings/preset": lambda: core.light_preset(),
            "/api/emu-settings/restore": lambda: core.restore_emu_backup(),
            "/api/fix": lambda: core.fix(str(body.get("name", ""))),
            "/api/updates/install": lambda: core.install_update(),
        }
        self._dispatch(routes.get(path))

    def _dispatch(self, handler) -> None:
        if handler is None:
            return self._error(404, "Action inconnue")
        try:
            result = handler()
        except ActionError as exc:
            return self._error(400, str(exc), exc.fix)
        except Exception as exc:  # on garde l'interface en vie et on journalise
            log.exception("Erreur sur %s", self.path)
            return self._error(500, f"Erreur interne : {exc}")
        self._json(200, result)


def make_server(core: Core, port: int) -> AppServer:
    return AppServer(("127.0.0.1", port), core)


def serve_in_thread(server: AppServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    return thread
