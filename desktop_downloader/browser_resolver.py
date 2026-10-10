from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket


MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".m3u8", ".mpd", ".mp3", ".m4a", ".aac", ".ogg", ".opus")
MEDIA_MIME_PREFIXES = ("video/", "audio/")
MEDIA_MIME_EXACT = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
}


def _browser_candidates() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ
    roots = [
        env.get("PROGRAMFILES"),
        env.get("PROGRAMFILES(X86)"),
        env.get("LOCALAPPDATA"),
    ]
    relative = [
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
    ]
    for root in roots:
        if not root:
            continue
        for child in relative:
            candidates.append(Path(root) / child)
    for executable in ("chrome.exe", "msedge.exe", "brave.exe", "chrome", "microsoft-edge"):
        found = shutil.which(executable)
        if found:
            candidates.append(Path(found))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            unique.append(candidate)
    return unique


def find_browser() -> Path | None:
    candidates = _browser_candidates()
    return candidates[0] if candidates else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _devtools_json(port: int, path: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _looks_like_media(url: str, mime_type: str) -> bool:
    lower_url = str(url or "").lower().split("?", 1)[0]
    mime = str(mime_type or "").lower().split(";", 1)[0].strip()
    if mime.startswith(MEDIA_MIME_PREFIXES) or mime in MEDIA_MIME_EXACT:
        return True
    return lower_url.endswith(MEDIA_EXTENSIONS)


def _format_from_response(page_url: str, response: dict[str, Any]) -> dict[str, Any] | None:
    url = str(response.get("url") or "")
    mime = str(response.get("mimeType") or "")
    if not url.startswith(("http://", "https://")) or not _looks_like_media(url, mime):
        return None

    lower = url.lower().split("?", 1)[0]
    protocol = "https"
    ext = "mp4"
    if lower.endswith(".m3u8") or "mpegurl" in mime.lower():
        protocol = "m3u8_native"
        ext = "mp4"
    elif lower.endswith(".mpd") or "dash+xml" in mime.lower():
        protocol = "http_dash_segments"
        ext = "mp4"
    elif "." in lower.rsplit("/", 1)[-1]:
        candidate = lower.rsplit(".", 1)[-1]
        if 1 <= len(candidate) <= 5:
            ext = candidate

    return {
        "format_id": f"browser-{abs(hash(url)) % 1000000}",
        "url": url,
        "ext": ext,
        "protocol": protocol,
        "http_headers": {
            "Referer": page_url,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
        },
    }


class _CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=1.0,
            origin="http://127.0.0.1",
        )
        self.counter = 0
        self.pending: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def command(self, method: str, params: dict[str, Any] | None = None, timeout: float = 4.0) -> dict[str, Any]:
        self.counter += 1
        command_id = self.counter
        self.ws.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._recv(max(0.1, deadline - time.monotonic()))
            if message is None:
                continue
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return dict(message.get("result") or {})
            self.pending.append(message)
        raise RuntimeError(f"Browser command timed out: {method}")

    def _recv(self, timeout: float = 0.5) -> dict[str, Any] | None:
        try:
            self.ws.settimeout(max(0.05, timeout))
            payload = self.ws.recv()
        except Exception:
            return None
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def event(self, timeout: float = 0.5) -> dict[str, Any] | None:
        if self.pending:
            return self.pending.pop(0)
        return self._recv(timeout)


def resolve_with_installed_browser(
    url: str,
    *,
    cancel_event=None,
    capture_seconds: float = 12.0,
) -> dict[str, Any]:
    browser = find_browser()
    if browser is None:
        raise RuntimeError("Chrome, Edge or Brave was not found for browser fallback.")

    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="MediaDownloaderBrowser_") as profile:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--autoplay-policy=no-user-gesture-required",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        cdp = None
        try:
            deadline = time.monotonic() + 10.0
            pages = None
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Analysis cancelled.")
                try:
                    pages = _devtools_json(port, "/json/list", timeout=0.8)
                    if pages:
                        break
                except Exception:
                    time.sleep(0.15)
            if not pages:
                raise RuntimeError("The installed browser did not start its resolver session.")

            page = next((item for item in pages if item.get("type") == "page"), pages[0])
            ws_url = str(page.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                raise RuntimeError("Browser resolver did not expose a DevTools page.")

            cdp = _CDP(ws_url)
            cdp.command("Network.enable")
            cdp.command("Page.enable")
            cdp.command("Runtime.enable")
            cdp.command("Page.navigate", {"url": url}, timeout=6.0)

            formats: list[dict[str, Any]] = []
            seen: set[str] = set()
            title = "Browser media"
            started = time.monotonic()
            played = False

            while time.monotonic() - started < capture_seconds:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Analysis cancelled.")

                message = cdp.event(timeout=0.35)
                if message:
                    method = str(message.get("method") or "")
                    params = dict(message.get("params") or {})
                    if method == "Network.responseReceived":
                        response = dict(params.get("response") or {})
                        item = _format_from_response(url, response)
                        if item and item["url"] not in seen:
                            seen.add(item["url"])
                            formats.append(item)

                elapsed = time.monotonic() - started
                if not played and elapsed > 2.0:
                    played = True
                    try:
                        cdp.command(
                            "Runtime.evaluate",
                            {
                                "expression": (
                                    "(()=>{for(const v of document.querySelectorAll('video')){"
                                    "try{v.muted=true;v.autoplay=true;v.play().catch(()=>{});}catch(e){}}"
                                    "return document.title||'';})()"
                                ),
                                "returnByValue": True,
                            },
                            timeout=2.0,
                        )
                    except Exception:
                        pass

                if formats and elapsed > 5.0:
                    break

            try:
                result = cdp.command(
                    "Runtime.evaluate",
                    {"expression": "document.title || ''", "returnByValue": True},
                    timeout=2.0,
                )
                title = str(((result.get("result") or {}).get("value")) or title)
            except Exception:
                pass

            if not formats:
                raise RuntimeError("The browser loaded the page but did not observe a downloadable media stream.")

            return {
                "id": f"browser-{abs(hash(url))}",
                "display_id": f"browser-{abs(hash(url))}",
                "title": title,
                "webpage_url": url,
                "original_url": url,
                "extractor": "InstalledBrowserResolver",
                "extractor_key": "InstalledBrowserResolver",
                "uploader": "",
                "formats": formats,
            }
        finally:
            if cdp is not None:
                cdp.close()
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
