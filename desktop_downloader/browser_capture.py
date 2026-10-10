from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

DEFAULT_CAPTURE_PORT = 38471
MAX_CAPTURE_BODY = 128 * 1024
MAX_CAPTURES = 120
ALLOWED_HEADERS = {"referer", "origin", "user-agent", "cookie", "authorization", "accept-language"}
ALLOWED_KINDS = {"direct", "hls", "dash", "page", "unknown"}


def generate_capture_token() -> str:
    return secrets.token_urlsafe(24)


def classify_media_url(url: str, content_type: str = "") -> str:
    parsed = urlparse(str(url or "").strip())
    path = parsed.path.lower()
    content = str(content_type or "").lower().split(";", 1)[0].strip()

    if path.endswith(".m3u8") or content in {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
        "audio/x-mpegurl",
    }:
        return "hls"
    if path.endswith(".mpd") or content == "application/dash+xml":
        return "dash"
    if content.startswith("video/") or content.startswith("audio/"):
        return "direct"
    if path.endswith((".mp4", ".webm", ".mov", ".mkv", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav")):
        return "direct"
    return "unknown"


def sanitize_headers(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        name = str(key or "").strip().lower()
        if name not in ALLOWED_HEADERS:
            continue
        text = str(value or "").strip()
        if not text or len(text) > 16_384:
            continue
        canonical = {
            "referer": "Referer",
            "origin": "Origin",
            "user-agent": "User-Agent",
            "cookie": "Cookie",
            "authorization": "Authorization",
            "accept-language": "Accept-Language",
        }[name]
        result[canonical] = text
    return result


def sanitize_capture(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Capture payload must be a JSON object.")

    url = str(payload.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Capture URL must be a valid HTTP or HTTPS URL.")
    if len(url) > 16_384:
        raise ValueError("Capture URL is too long.")

    content_type = str(payload.get("content_type") or "").strip()[:200]
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in ALLOWED_KINDS:
        kind = classify_media_url(url, content_type)

    title = str(payload.get("title") or "").strip()[:500]
    page_url = str(payload.get("page_url") or "").strip()[:16_384]
    if page_url:
        page = urlparse(page_url)
        if page.scheme.lower() not in {"http", "https"}:
            page_url = ""

    try:
        tab_id = int(payload.get("tab_id") or 0)
    except (TypeError, ValueError):
        tab_id = 0

    try:
        duration = max(0, int(float(payload.get("duration_seconds") or 0)))
    except (TypeError, ValueError):
        duration = 0

    capture_group_id = str(payload.get("capture_group_id") or "").strip()[:120]

    try:
        frame_id = int(payload.get("frame_id") if payload.get("frame_id") is not None else -1)
    except (TypeError, ValueError):
        frame_id = -1
    try:
        width = max(0, int(float(payload.get("width") or 0)))
    except (TypeError, ValueError):
        width = 0
    try:
        height = max(0, int(float(payload.get("height") or 0)))
    except (TypeError, ValueError):
        height = 0
    try:
        fps = max(0.0, float(payload.get("fps") or 0))
    except (TypeError, ValueError):
        fps = 0.0
    try:
        tbr = max(0.0, float(payload.get("tbr") or 0))
    except (TypeError, ValueError):
        tbr = 0.0
    try:
        total_bytes = max(0, int(float(payload.get("total_bytes") or 0)))
    except (TypeError, ValueError):
        total_bytes = 0

    media_type = str(payload.get("media_type") or "").strip().lower()[:24]
    if media_type not in {"video", "audio", ""}:
        media_type = ""
    itag = str(payload.get("itag") or "").strip()[:32]

    qualities = payload.get("available_qualities")
    available_qualities = [
        str(value)[:32]
        for value in qualities
        if str(value or "").strip()
    ][:20] if isinstance(qualities, list) else []

    quality_status = str(payload.get("quality_status") or "").strip().lower()[:32]
    quality_label = str(payload.get("quality_label") or "").strip()[:80]

    return {
        "id": str(payload.get("id") or uuid.uuid4().hex),
        "captured_at": float(payload.get("captured_at") or time.time()),
        "url": url,
        "page_url": page_url,
        "title": title or parsed.hostname or "Captured media",
        "tab_id": tab_id,
        "frame_id": frame_id,
        "kind": kind,
        "content_type": content_type,
        "headers": sanitize_headers(payload.get("headers")),
        "duration_seconds": duration,
        "capture_group_id": capture_group_id,
        "width": width,
        "height": height,
        "fps": fps,
        "tbr": tbr,
        "total_bytes": total_bytes,
        "media_type": media_type,
        "itag": itag,
        "quality_status": quality_status,
        "quality_label": quality_label,
        "available_qualities": available_qualities,
        "has_multiple_qualities": bool(payload.get("has_multiple_qualities") or len(available_qualities) > 1),
    }


@dataclass
class CaptureStore:
    limit: int = MAX_CAPTURES
    _items: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def add(self, payload: Any) -> dict[str, Any]:
        item = sanitize_capture(payload)
        key = (item["tab_id"], item["url"])
        with self._lock:
            previous = next(
                (
                    existing
                    for existing in self._items
                    if (existing.get("tab_id"), existing.get("url")) == key
                ),
                None,
            )
            if previous:
                for metadata_key in (
                    "quality_status",
                    "quality_label",
                    "height",
                    "width",
                    "fps",
                    "tbr",
                    "total_bytes",
                    "media_type",
                    "itag",
                    "duration_seconds",
                    "available_qualities",
                    "has_multiple_qualities",
                ):
                    if metadata_key in previous:
                        item[metadata_key] = previous[metadata_key]

            self._items = [
                existing
                for existing in self._items
                if (existing.get("tab_id"), existing.get("url")) != key
            ]
            self._items.insert(0, item)
            del self._items[self.limit :]
        return dict(item)

    def update_metadata(self, capture_id: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        capture_id = str(capture_id or "")
        allowed = {
            "quality_status",
            "quality_label",
            "height",
            "width",
            "fps",
            "tbr",
            "total_bytes",
            "media_type",
            "itag",
            "duration_seconds",
            "available_qualities",
            "has_multiple_qualities",
        }
        clean = {key: value for key, value in dict(metadata or {}).items() if key in allowed}
        with self._lock:
            for item in self._items:
                if item.get("id") != capture_id:
                    continue
                item.update(clean)
                return dict(item)
        return None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def get(self, capture_id: str) -> dict[str, Any] | None:
        capture_id = str(capture_id or "")
        with self._lock:
            for item in self._items:
                if item.get("id") == capture_id:
                    return dict(item)
        return None

    def remove(self, capture_id: str) -> bool:
        capture_id = str(capture_id or "")
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.get("id") != capture_id]
            return len(self._items) != before

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count


class _CaptureHandler(BaseHTTPRequestHandler):
    server_version = "MediaDownloaderCapture/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin") or ""
        if origin.startswith(("chrome-extension://", "moz-extension://")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if str(self.headers.get("Access-Control-Request-Private-Network") or "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Media-Downloader-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self._send_json(204, {})

    def do_GET(self) -> None:
        bridge: BrowserCaptureBridge = self.server.bridge  # type: ignore[attr-defined]
        if self.path.rstrip("/") == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "Media Downloader Browser Capture",
                    "port": bridge.port,
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        bridge: BrowserCaptureBridge = self.server.bridge  # type: ignore[attr-defined]
        route = self.path.rstrip("/")
        if route not in {"/capture", "/capture-download"}:
            self._send_json(404, {"ok": False, "error": "Not found."})
            return

        supplied = str(self.headers.get("X-Media-Downloader-Token") or "")
        if not supplied or not hmac.compare_digest(supplied, bridge.token):
            self._send_json(401, {"ok": False, "error": "Invalid pairing token."})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_CAPTURE_BODY:
            self._send_json(413, {"ok": False, "error": "Capture payload is too large."})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            item = bridge.store.add(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception:
            self._send_json(500, {"ok": False, "error": "Could not store capture."})
            return

        if bridge.on_capture is not None:
            try:
                outgoing = dict(item)
                if route == "/capture-download":
                    outgoing["_browser_action"] = "download"
                bridge.on_capture(outgoing)
            except Exception:
                pass
        self._send_json(
            200,
            {
                "ok": True,
                "capture_id": item["id"],
                "action": "download" if route == "/capture-download" else "capture",
            },
        )

    def log_message(self, _format: str, *_args: Any) -> None:
        # Do not log signed URLs, cookies, Authorization headers, or query strings.
        return


class BrowserCaptureBridge:
    def __init__(
        self,
        store: CaptureStore,
        token: str,
        *,
        port: int = DEFAULT_CAPTURE_PORT,
        on_capture: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.token = str(token)
        self.requested_port = int(port)
        self.port = 0
        self.on_capture = on_capture
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> int:
        if self.running:
            return self.port

        last_error: OSError | None = None
        for candidate in range(self.requested_port, self.requested_port + 10):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", candidate), _CaptureHandler)
                server.daemon_threads = True
                server.bridge = self  # type: ignore[attr-defined]
                self._server = server
                self.port = int(server.server_address[1])
                break
            except OSError as exc:
                last_error = exc
        else:
            raise OSError(f"Could not bind browser capture bridge: {last_error}")

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="browser-capture-bridge",
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        self._thread = None
        self.port = 0
