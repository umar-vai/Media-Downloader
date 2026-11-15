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


SEGMENT_MIME_TYPES = {"video/mp2t", "video/iso.segment", "audio/mp2t"}
SEGMENT_EXTENSIONS = (".ts", ".m4s", ".m4f", ".cmfv", ".cmfa")


def _looks_like_media(url: str, mime_type: str) -> bool:
    lower_url = str(url or "").lower().split("?", 1)[0]
    mime = str(mime_type or "").lower().split(";", 1)[0].strip()
    # Single HLS/DASH segments are pieces of a stream, never a whole video.
    if mime in SEGMENT_MIME_TYPES or lower_url.endswith(SEGMENT_EXTENSIONS):
        return False
    if mime.startswith(MEDIA_MIME_PREFIXES) or mime in MEDIA_MIME_EXACT:
        return True
    return lower_url.endswith(MEDIA_EXTENSIONS)


def _format_from_url(page_url: str, media_url: str, mime_type: str = "") -> dict[str, Any] | None:
    return _format_from_response(
        page_url,
        {
            "url": media_url,
            "mimeType": mime_type,
        },
    )


def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _add_cookie_header(item: dict[str, Any], cookie_header: str) -> dict[str, Any]:
    if not cookie_header:
        return item
    headers = dict(item.get("http_headers") or {})
    headers["Cookie"] = cookie_header
    item["http_headers"] = headers
    return item


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
    """Minimal DevTools client. Child targets (cross-origin player iframes, workers) are
    attached in "flatten" mode, so their commands and events share this one socket and
    carry a ``sessionId``."""

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

    def send(self, method: str, params: dict[str, Any] | None = None, session_id: str = "") -> int:
        """Send a command without waiting for its answer; returns the command id."""
        self.counter += 1
        message: dict[str, Any] = {"id": self.counter, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self.ws.send(json.dumps(message))
        return self.counter

    def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 4.0,
        session_id: str = "",
    ) -> dict[str, Any]:
        command_id = self.send(method, params, session_id)
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


def _rank_embed_urls(urls: list[str], page_url: str) -> list[str]:
    page_host = ""
    try:
        from urllib.parse import urlparse

        page_host = (urlparse(page_url).hostname or "").lower()
    except Exception:
        pass

    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    keywords = ("embed", "player", "stream", "watch", "video", "episode", "server")
    for raw in urls:
        value = str(raw or "").strip()
        if not value.startswith(("http://", "https://")) or value in seen or value == page_url:
            continue
        seen.add(value)
        score = sum(3 for key in keywords if key in value.lower())
        try:
            from urllib.parse import urlparse

            host = (urlparse(value).hostname or "").lower()
            if host and host != page_host:
                score += 2
        except Exception:
            pass
        ranked.append((score, value))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [value for _score, value in ranked]


def _stream_kind(url: str, mime: str) -> str:
    lower = str(url or "").lower().split("?", 1)[0]
    mime = str(mime or "").lower()
    if lower.endswith(".m3u8") or "mpegurl" in mime:
        return "hls"
    if lower.endswith(".mpd") or "dash+xml" in mime:
        return "dash"
    return "file"


def hls_master_children(text: str, base_url: str) -> list[str] | None:
    """Variant/rendition playlist URLs of an HLS master playlist, or None for a media playlist."""
    from urllib.parse import urljoin

    if "#EXT-X-STREAM-INF" not in str(text or ""):
        return None
    children: list[str] = []
    expect_uri = False
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            expect_uri = True
            continue
        if line.startswith(("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF")):
            marker = 'URI="'
            start = line.find(marker)
            if start >= 0:
                end = line.find('"', start + len(marker))
                if end > start:
                    children.append(urljoin(base_url, line[start + len(marker):end]))
            continue
        if line.startswith("#"):
            continue
        if expect_uri:
            children.append(urljoin(base_url, line))
            expect_uri = False
    return children


def order_streams(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop playlists that a captured master already lists, and put masters first.

    Players load the master playlist and then one variant (often 720p, video only, with the
    audio in a separate rendition). Downloading that variant gives a low-quality, silent file,
    so the master is the stream to use."""
    children: set[str] = set()
    for stream in streams:
        children.update(stream.get("children") or [])
    kept = [stream for stream in streams if stream.get("children") is not None or stream["url"] not in children]
    rank = {"hls": 1, "dash": 1, "file": 2}

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        index, stream = item
        is_master = 0 if stream.get("children") is not None else 1
        return (rank.get(str(stream.get("kind")), 3), is_master, index)

    return [stream for _index, stream in sorted(enumerate(kept), key=key)]


_PLAY_SCRIPT = (
    "(()=>{"
    "for(const v of document.querySelectorAll('video')){"
    "try{v.muted=true;if(v.paused)v.play().catch(()=>{});}catch(e){}}"
    "const selectors=["
    "'.vjs-big-play-button','.jw-icon-display','.jw-display-icon-container',"
    "'.plyr__control--overlaid','.play-button','.play-btn','.art-control-play',"
    "'[aria-label*=play i]','button[class*=play i]','div[class*=play i]'];"
    "if(!document.querySelector('video')||[...document.querySelectorAll('video')].every(v=>v.paused)){"
    "for(const s of selectors){const e=document.querySelector(s);"
    "if(e){try{e.click();}catch(x){}}}}"
    "return document.title||'';"
    "})()"
)

_SCAN_SCRIPT = (
    "(()=>{"
    "const media=new Set(),frames=new Set();"
    "for(const v of document.querySelectorAll('video')){"
    "for(const x of [v.currentSrc,v.src]) if(x) media.add(x);"
    "for(const s of v.querySelectorAll('source')) if(s.src) media.add(s.src);"
    "}"
    "for(const s of document.querySelectorAll('source')) if(s.src) media.add(s.src);"
    "for(const e of performance.getEntriesByType('resource')){"
    "const n=e&&e.name||'';"
    "if(/\\.(?:m3u8|mpd|mp4|m4v|webm|m4a|mp3)(?:[?#]|$)/i.test(n)) media.add(n);"
    "}"
    "for(const f of document.querySelectorAll('iframe[src]')){if(f.src) frames.add(f.src);}"
    "return {media:[...media],frames:[...frames],title:document.title||'',href:location.href};"
    "})()"
)

_FORWARD_HEADERS = {"referer": "Referer", "origin": "Origin", "user-agent": "User-Agent"}


def resolve_with_installed_browser(
    url: str,
    *,
    cancel_event=None,
    capture_seconds: float = 18.0,
) -> dict[str, Any]:
    """Open ``url`` in a hidden Chrome/Edge/Brave and capture the media it plays.

    Also listens inside cross-origin player iframes (e.g. an anime site that embeds a
    megaplay/vidstream player) and records the exact Referer/Origin each stream was
    requested with, because those CDNs refuse requests without them."""
    browser = find_browser()
    if browser is None:
        raise RuntimeError("Chrome, Edge or Brave was not found for browser fallback.")

    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="MediaDownloaderBrowser_") as profile:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-popup-blocking",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=MediaEngagementBypassAutoplayPolicies",
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

            # Headless Chrome announces itself as "HeadlessChrome"; many players refuse that.
            user_agent = ""
            try:
                user_agent = str(_devtools_json(port, "/json/version").get("User-Agent") or "")
            except Exception:
                pass
            user_agent = user_agent.replace("HeadlessChrome", "Chrome") or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
            )

            cdp = _CDP(ws_url)
            cdp.command("Network.enable")
            cdp.command("Page.enable")
            try:
                cdp.command("Network.setUserAgentOverride", {"userAgent": user_agent})
            except Exception:
                pass
            auto_attach = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
            try:
                cdp.command("Target.setAutoAttach", auto_attach)
            except Exception:
                pass

            streams: list[dict[str, Any]] = []
            by_url: dict[str, dict[str, Any]] = {}
            requests: dict[tuple[str, str], dict[str, Any]] = {}
            frame_sessions: dict[str, str] = {"": url}  # sessionId -> document URL ("" = top page)
            embed_urls: list[str] = []
            scan_ids: dict[int, str] = {}
            title = "Browser media"

            def note_request(sid: str, request_id: str, headers: dict[str, Any], document: str = "") -> None:
                entry = requests.setdefault((sid, request_id), {"headers": {}, "document": ""})
                for name, value in (headers or {}).items():
                    entry["headers"][str(name).lower()] = str(value)
                if document:
                    entry["document"] = document
                if len(requests) > 4000:
                    requests.pop(next(iter(requests)))

            def add_stream(page_url: str, media_url: str, mime: str, sid: str = "", request_id: str = "") -> None:
                item = _format_from_response(page_url, {"url": media_url, "mimeType": mime})
                if not item or media_url in by_url:
                    return
                request = requests.get((sid, request_id)) or {"headers": {}, "document": ""}
                sent = request["headers"]
                headers: dict[str, str] = {}
                for lower, proper in _FORWARD_HEADERS.items():
                    if sent.get(lower):
                        headers[proper] = sent[lower]
                headers.setdefault("Referer", request["document"] or frame_sessions.get(sid) or page_url)
                headers.setdefault("User-Agent", user_agent)
                stream = {
                    "url": media_url,
                    "kind": _stream_kind(media_url, mime),
                    "headers": headers,
                    "session": sid,
                    "request_id": request_id,
                    "children": None,
                }
                by_url[media_url] = stream
                streams.append(stream)

            def setup_child(params: dict[str, Any]) -> None:
                sid = str(params.get("sessionId") or "")
                target = dict(params.get("targetInfo") or {})
                kind = str(target.get("type") or "")
                if not sid:
                    return
                try:
                    if kind in {"iframe", "page"}:
                        frame_sessions[sid] = str(target.get("url") or "")
                        cdp.send("Network.enable", {}, sid)
                        cdp.send("Network.setUserAgentOverride", {"userAgent": user_agent}, sid)
                        cdp.send("Target.setAutoAttach", auto_attach, sid)
                    elif kind in {"worker", "shared_worker", "service_worker"}:
                        cdp.send("Network.enable", {}, sid)
                    cdp.send("Runtime.runIfWaitingForDebugger", {}, sid)
                except Exception:
                    pass

            def handle(message: dict[str, Any], page_url: str) -> None:
                nonlocal title
                if "id" in message and "method" not in message:
                    owner = scan_ids.pop(int(message.get("id") or 0), None)
                    if owner is None:
                        return
                    value = ((message.get("result") or {}).get("result") or {}).get("value") or {}
                    if not isinstance(value, dict):
                        return
                    referer = str(value.get("href") or frame_sessions.get(owner) or page_url)
                    for media_url in list(value.get("media") or []):
                        media_url = str(media_url or "")
                        if media_url.startswith(("http://", "https://")) and media_url not in by_url:
                            add_stream(referer, media_url, "", owner)
                            if media_url in by_url:
                                by_url[media_url]["headers"]["Referer"] = referer
                    for frame_url in list(value.get("frames") or []):
                        frame_url = str(frame_url or "")
                        if frame_url.startswith(("http://", "https://")) and frame_url not in embed_urls:
                            embed_urls.append(frame_url)
                    if owner == "" and value.get("title"):
                        title = str(value.get("title"))
                    return
                method = str(message.get("method") or "")
                params = dict(message.get("params") or {})
                sid = str(message.get("sessionId") or "")
                if method == "Target.attachedToTarget":
                    setup_child(params)
                elif method == "Target.detachedFromTarget":
                    frame_sessions.pop(str(params.get("sessionId") or ""), None)
                elif method == "Network.requestWillBeSent":
                    request = dict(params.get("request") or {})
                    note_request(sid, str(params.get("requestId") or ""), dict(request.get("headers") or {}),
                                 str(params.get("documentURL") or ""))
                elif method == "Network.requestWillBeSentExtraInfo":
                    note_request(sid, str(params.get("requestId") or ""), dict(params.get("headers") or {}))
                elif method == "Network.responseReceived":
                    response = dict(params.get("response") or {})
                    status = int(response.get("status") or 0)
                    if status and status >= 400:
                        return
                    add_stream(page_url, str(response.get("url") or ""), str(response.get("mimeType") or ""),
                               sid, str(params.get("requestId") or ""))

            def capture_page(target_url: str, seconds: float, referrer: str = "") -> None:
                navigate = {"url": target_url}
                if referrer:
                    navigate["referrer"] = referrer
                cdp.command("Page.navigate", navigate, timeout=7.0)
                started = time.monotonic()
                first_stream_at = 0.0
                last_play = -10.0
                last_scan_bucket = -1

                while time.monotonic() - started < seconds:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Analysis cancelled.")

                    message = cdp.event(timeout=0.3)
                    if message:
                        handle(message, target_url)

                    elapsed = time.monotonic() - started
                    if streams and not first_stream_at:
                        first_stream_at = elapsed

                    # Start the players: the top page and every attached player iframe.
                    if elapsed > 1.5 and elapsed - last_play > 3.0 and elapsed < 16.0:
                        last_play = elapsed
                        for sid in list(frame_sessions):
                            try:
                                cdp.send("Runtime.evaluate", {"expression": _PLAY_SCRIPT, "returnByValue": True}, sid)
                            except Exception:
                                pass

                    scan_bucket = int(elapsed // 2)
                    if elapsed > 2.0 and scan_bucket != last_scan_bucket:
                        last_scan_bucket = scan_bucket
                        for sid in list(frame_sessions):
                            try:
                                command_id = cdp.send(
                                    "Runtime.evaluate",
                                    {"expression": _SCAN_SCRIPT, "returnByValue": True},
                                    sid,
                                )
                                scan_ids[command_id] = sid
                            except Exception:
                                pass

                    if first_stream_at:
                        has_stream = any(item["kind"] in {"hls", "dash"} for item in streams)
                        # A player loads the master playlist and its variants within a second or two.
                        if has_stream and elapsed - first_stream_at > 3.0:
                            break
                        if not has_stream and elapsed > 6.0 and elapsed - first_stream_at > 2.0:
                            break

            capture_page(url, capture_seconds)

            if not streams and embed_urls:
                for embed_url in _rank_embed_urls(embed_urls, url)[:4]:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Analysis cancelled.")
                    try:
                        frame_sessions[""] = embed_url
                        capture_page(embed_url, 9.0, referrer=url)
                    except Exception:
                        continue
                    if streams:
                        break

            if not streams:
                raise RuntimeError(
                    "The browser loaded the page and embedded players but did not observe a downloadable media stream."
                )

            # Read the captured playlists so the master (all qualities + audio) wins over the
            # single variant the player happened to start with.
            for stream in streams:
                if stream["kind"] != "hls" or not stream.get("request_id"):
                    continue
                try:
                    body = cdp.command(
                        "Network.getResponseBody",
                        {"requestId": stream["request_id"]},
                        timeout=2.5,
                        session_id=stream["session"],
                    )
                    text = str(body.get("body") or "")
                    if body.get("base64Encoded"):
                        import base64

                        text = base64.b64decode(text).decode("utf-8", errors="replace")
                    stream["children"] = hls_master_children(text, stream["url"])
                    stream["drm"] = "SAMPLE-AES" in text.upper() and "#EXT-X-KEY" in text.upper()
                except Exception:
                    pass
            ordered = order_streams(streams)

            formats: list[dict[str, Any]] = []
            browser_streams: list[dict[str, Any]] = []
            for stream in ordered:
                headers = dict(stream["headers"])
                try:
                    cookie_result = cdp.command(
                        "Network.getCookies",
                        {"urls": [url, headers.get("Referer") or url, stream["url"]]},
                        timeout=2.0,
                    )
                    cookie_header = _cookie_header(list(cookie_result.get("cookies") or []))
                    if cookie_header:
                        headers["Cookie"] = cookie_header
                except Exception:
                    pass
                item = _format_from_url(url, stream["url"])
                if item is None:
                    continue
                item["http_headers"] = headers
                formats.append(item)
                browser_streams.append(
                    {
                        "url": stream["url"],
                        "kind": stream["kind"],
                        "master": stream.get("children") is not None,
                        "drm": bool(stream.get("drm")),
                        "http_headers": headers,
                    }
                )

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
                "_browser_streams": browser_streams,
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
