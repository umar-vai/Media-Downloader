"""Local-only HTTP endpoint used by the Media Downloader browser extension.

Listens on 127.0.0.1 (ports 47720-47729), requires a per-install token and only accepts
requests that come from a browser extension (or tools with no Origin header).
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

PORTS = range(47720, 47730)
MAX_BODY = 512 * 1024
OnDownload = Callable[[dict[str, Any]], bool]


def cookies_to_netscape(cookies: list[dict[str, Any]]) -> str:
    """Convert chrome.cookies.getAll() objects to a Netscape cookies.txt string."""
    lines = ["# Netscape HTTP Cookie File"]
    for item in cookies:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "")
        name = str(item.get("name") or "")
        if not domain or not name:
            continue
        value = str(item.get("value") or "")
        if any(ch in value + name + domain for ch in "\r\n\t"):
            continue
        host_only = bool(item.get("hostOnly"))
        flag = "FALSE" if host_only else "TRUE"
        if not host_only and not domain.startswith("."):
            domain = "." + domain
        path = str(item.get("path") or "/")
        secure = "TRUE" if item.get("secure") else "FALSE"
        try:
            expires = int(float(item.get("expirationDate") or 0))
        except (TypeError, ValueError):
            expires = 0
        if expires <= 0:
            expires = int(time.time()) + 7 * 24 * 3600
        prefix = "#HttpOnly_" if item.get("httpOnly") else ""
        lines.append("\t".join([prefix + domain, flag, path, secure, str(expires), name, value]))
    return "\n".join(lines) + "\n"


class ExtensionServer:
    def __init__(self, *, token: str, on_download: OnDownload, app_version: str) -> None:
        self.token = token
        self._on_download = on_download
        self._version = app_version
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> bool:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MediaDownloader"
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # silence default stderr logging
                return

            def _origin_ok(self) -> bool:
                origin = self.headers.get("Origin") or ""
                return not origin or origin.startswith(("chrome-extension://", "moz-extension://", "edge-extension://"))

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                origin = self.headers.get("Origin") or ""
                if origin and self._origin_ok():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204 if self._origin_ok() else 403)
                origin = self.headers.get("Origin") or ""
                if origin and self._origin_ok():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MD-Token")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if not self._origin_ok():
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                if urlsplit(self.path).path == "/ping":
                    self._send(200, {"ok": True, "app": "Media Downloader", "version": owner._version})
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._origin_ok():
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                if urlsplit(self.path).path != "/download":
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                supplied = self.headers.get("X-MD-Token") or ""
                if not owner.token or not hmac.compare_digest(supplied.encode(), owner.token.encode()):
                    self._send(401, {"ok": False, "error": "bad token"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_BODY:
                    self._send(413, {"ok": False, "error": "bad size"})
                    return
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError
                except Exception:
                    self._send(400, {"ok": False, "error": "bad json"})
                    return
                url = str(data.get("url") or "").strip()
                if urlsplit(url).scheme.lower() not in {"http", "https"}:
                    self._send(400, {"ok": False, "error": "bad url"})
                    return
                try:
                    accepted = bool(owner._on_download(data))
                except Exception:
                    accepted = False
                self._send(200 if accepted else 500, {"ok": accepted})

        for port in PORTS:
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self.port = port
                break
            except OSError:
                continue
        if self._server is None:
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="extension-server", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass


def write_cookie_file(directory: Path, request_id: str, cookies: list[dict[str, Any]]) -> Path | None:
    if not cookies:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{request_id}.txt"
    path.write_text(cookies_to_netscape(cookies), encoding="utf-8")
    return path


def purge_old_cookie_files(directory: Path, max_age_seconds: float = 24 * 3600) -> None:
    try:
        for path in directory.glob("*.txt"):
            if time.time() - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
    except OSError:
        pass
