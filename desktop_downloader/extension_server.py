"""Local-only HTTP endpoint used by the Media Downloader browser extension.

Listens on 127.0.0.1 (ports 47720-47729), requires a per-install token and only accepts
requests that come from a browser extension (or tools with no Origin header).
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

PORTS = range(47720, 47730)
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "edge-extension://", "extension://")
MAX_BODY = 512 * 1024
OnDownload = Callable[[dict[str, Any]], bool]
RESOLVE_MARKER = "md-resolve"
_JOB_ID_RE = re.compile(r"^[0-9a-f]{16,40}$")


def resolve_page_url(url: str, job_id: str) -> str:
    """The page URL with a fragment marker the extension recognises (it never reaches the site)."""
    separator = "&" if "#" in url else "#"
    return f"{url}{separator}{RESOLVE_MARKER}={job_id}"


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


class _ExclusiveServer(ThreadingHTTPServer):
    """Never share a port: on Windows SO_REUSEADDR lets a second process bind the same port."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class ExtensionServer:
    def __init__(
        self,
        *,
        token: str,
        on_download: OnDownload,
        app_version: str,
        on_analyze: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        capture: bool = True,
    ) -> None:
        self.token = token
        # Whether the extension should take over the browser's own downloads. The listener
        # itself always runs, so the right-click menu and the video button keep working.
        self.capture = capture
        self._on_download = on_download
        self._on_analyze = on_analyze
        self._version = app_version
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.extension_seen_at = 0.0
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()

    # ------------------------------------------------------------------ browser bridge
    @property
    def extension_connected(self) -> bool:
        return bool(self.extension_seen_at)

    def extension_active(self, within: float = 300.0) -> bool:
        """True when the extension talked to the app in the last ``within`` seconds."""
        return bool(self.extension_seen_at) and time.time() - self.extension_seen_at < within

    @property
    def running(self) -> bool:
        return self._server is not None

    def resolve_in_browser(
        self,
        url: str,
        *,
        cancel_event: threading.Event | None = None,
        timeout: float = 45.0,
        opener: Callable[[str], Any] | None = None,
    ) -> dict[str, Any] | None:
        """Open ``url`` in the user's own browser and wait for the extension to report the
        streams the player loads there. Returns the extension's report, or None."""
        if not self.extension_connected or self._server is None:
            return None
        job_id = secrets.token_hex(12)
        job: dict[str, Any] = {"url": url, "event": threading.Event(), "result": None}
        with self._jobs_lock:
            self._jobs[job_id] = job
        try:
            (opener or webbrowser.open)(resolve_page_url(url, job_id))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return None
                if job["event"].wait(0.25):
                    return job["result"]
            return None
        finally:
            with self._jobs_lock:
                self._jobs.pop(job_id, None)

    def _finish_job(self, data: dict[str, Any]) -> bool:
        job_id = str(data.get("id") or "")
        if not _JOB_ID_RE.match(job_id):
            return False
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job["result"] = data
        job["event"].set()
        return True

    def start(self) -> bool:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MediaDownloader"
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # silence default stderr logging
                return

            def _origin_ok(self) -> bool:
                origin = self.headers.get("Origin") or ""
                return not origin or origin.startswith(EXTENSION_ORIGINS)

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
                route = urlsplit(self.path).path
                if route == "/ping":
                    self._send(200, {"ok": True, "app": "Media Downloader", "version": owner._version,
                                     "capture": bool(owner.capture)})
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._origin_ok():
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                route = urlsplit(self.path).path
                if route not in {"/download", "/hello", "/resolved", "/analyze"}:
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
                owner.extension_seen_at = time.time()
                if route == "/hello":
                    self._send(200, {"ok": True, "version": owner._version})
                    return
                if route == "/resolved":
                    accepted = owner._finish_job(data)
                    self._send(200 if accepted else 404, {"ok": accepted})
                    return
                url = str(data.get("url") or "").strip()
                try:
                    source = urlsplit(url)
                    valid_url = source.scheme.lower() in {"http", "https"} and bool(source.hostname)
                except ValueError:
                    valid_url = False
                if not valid_url:
                    self._send(400, {"ok": False, "error": "bad url"})
                    return
                if route == "/analyze":
                    if owner._on_analyze is None:
                        self._send(404, {"ok": False, "error": "not available"})
                        return
                    try:
                        result = dict(owner._on_analyze(data) or {})
                    except Exception as exc:  # never leave the extension waiting on an error page
                        result = {"ok": False, "error": str(exc)[:300] or "Analysis failed."}
                    self._send(200, result)
                    return
                try:
                    accepted = bool(owner._on_download(data))
                except Exception:
                    accepted = False
                if accepted:
                    self._send(200, {"ok": True})
                else:
                    self._send(409, {"ok": False, "error": "The desktop app could not accept the download."})

        for port in PORTS:
            try:
                self._server = _ExclusiveServer(("127.0.0.1", port), Handler)
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
