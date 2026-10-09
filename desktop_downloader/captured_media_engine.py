from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from browser_capture import sanitize_capture, sanitize_headers
from media_editor_engine import ffmpeg_exe
from network_proxy import active_proxy_url, yt_dlp_proxy_options

INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class CaptureDownloadCancelled(RuntimeError):
    pass


def safe_capture_name(value: str, fallback: str = "captured_media") -> str:
    name = INVALID_FILENAME.sub("_", str(value or "").strip()).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    return name[:160]


def capture_host(capture: dict[str, Any], *, prefer_page: bool = False) -> str:
    key = "page_url" if prefer_page and capture.get("page_url") else "url"
    try:
        return (urlparse(str(capture.get(key) or "")).hostname or "").lower()
    except Exception:
        return ""


def capture_media_mode(capture: dict[str, Any]) -> str:
    content = str(capture.get("content_type") or "").lower()
    if content.startswith("audio/"):
        return "Audio"
    try:
        path = urlparse(str(capture.get("url") or "")).path.lower()
    except Exception:
        path = ""
    if path.endswith((".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")):
        return "Audio"
    return "Video"


def _safe_download_error(exc: BaseException, capture: dict[str, Any]) -> str:
    host = capture_host(capture) or "media host"
    raw = str(exc) or exc.__class__.__name__
    url = str(capture.get("url") or "")
    page_url = str(capture.get("page_url") or "")
    if url:
        raw = raw.replace(url, f"https://{host}/…")
    if page_url:
        page_host = capture_host(capture, prefer_page=True) or "page"
        raw = raw.replace(page_url, f"https://{page_host}/…")
    raw = re.sub(r"https?://[^\s\]\)>'\"]+", lambda match: match.group(0).split("?", 1)[0], raw)
    return raw[-600:]


def capture_transport_profiles() -> list[tuple[str, dict[str, Any]]]:
    """Ordered network fallbacks for captured media.

    The user's explicit Windows proxy is preferred. Chrome/curl_cffi TLS is
    tried first because some video CDNs/proxies terminate Python's standard
    TLS connection early. If that still fails, use a low-concurrency
    compatibility pass, then an explicit direct route. On systems using a TUN
    engine, the direct socket is still captured by the TUN driver while
    avoiding an incompatible WinINet proxy CONNECT path.
    """
    proxy = active_proxy_url()
    profiles: list[tuple[str, dict[str, Any]]] = []
    chrome = ImpersonateTarget("chrome")

    if proxy:
        profiles.extend(
            [
                (
                    "Proxy • Chrome TLS",
                    {
                        "proxy": proxy,
                        "impersonate": chrome,
                        "concurrent_fragment_downloads": 4,
                    },
                ),
                (
                    "Proxy • compatibility",
                    {
                        "proxy": proxy,
                        "concurrent_fragment_downloads": 1,
                    },
                ),
                (
                    "TUN/direct • Chrome TLS",
                    {
                        "proxy": "",
                        "impersonate": chrome,
                        "source_address": "0.0.0.0",
                        "concurrent_fragment_downloads": 4,
                    },
                ),
                (
                    "TUN/direct • compatibility",
                    {
                        "proxy": "",
                        "source_address": "0.0.0.0",
                        "concurrent_fragment_downloads": 1,
                    },
                ),
            ]
        )
    else:
        profiles.extend(
            [
                (
                    "Chrome TLS",
                    {
                        "proxy": "",
                        "impersonate": chrome,
                        "source_address": "0.0.0.0",
                        "concurrent_fragment_downloads": 4,
                    },
                ),
                (
                    "Compatibility",
                    {
                        "proxy": "",
                        "source_address": "0.0.0.0",
                        "concurrent_fragment_downloads": 1,
                    },
                ),
            ]
        )
    return profiles


def _transport_error_hint(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    markers = (
        "unexpected_eof_while_reading",
        "unexpected eof",
        "ssl",
        "tls",
        "certificate",
        "proxy",
        "connection reset",
        "connection aborted",
        "remote end closed",
        "handshake",
        "eof occurred in violation of protocol",
    )
    return any(marker in text for marker in markers)


def capture_download_options(
    capture: dict[str, Any],
    output_dir: Path,
    base_name: str,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    transport_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = sanitize_capture(capture)
    output_dir = Path(output_dir)
    name = safe_capture_name(base_name)

    headers = sanitize_headers(item.get("headers"))
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "continuedl": True,
        "overwrites": False,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "http_headers": headers,
        **yt_dlp_proxy_options(),
        "outtmpl": str(output_dir / f"{name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe(),
        "merge_output_format": "mp4",
        "format": "bestvideo*+bestaudio/best",
    }
    if transport_options:
        options.update(dict(transport_options))
    if progress_hook is not None:
        options["progress_hooks"] = [progress_hook]
    return options


def _candidate_paths(output_dir: Path, base_name: str, started_at: float) -> list[Path]:
    prefix = safe_capture_name(base_name)
    candidates: list[Path] = []
    try:
        for path in output_dir.iterdir():
            if not path.is_file() or path.name.endswith((".part", ".ytdl")):
                continue
            if not path.stem.startswith(prefix):
                continue
            try:
                if path.stat().st_mtime >= started_at - 2:
                    candidates.append(path)
            except OSError:
                continue
    except OSError:
        return []
    return candidates


def cleanup_failed_capture_parts(output_dir: Path, base_name: str, started_at: float) -> None:
    prefix = safe_capture_name(base_name)
    try:
        for path in Path(output_dir).iterdir():
            if not path.is_file():
                continue
            if not path.name.startswith(prefix):
                continue
            if not path.name.endswith((".part", ".ytdl", ".temp", ".tmp")):
                continue
            try:
                if path.stat().st_mtime >= started_at - 2:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def download_captured_media(
    capture: dict[str, Any],
    output_dir: Path,
    *,
    base_name: str | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> Path:
    item = sanitize_capture(capture)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = safe_capture_name(base_name or item.get("title") or "captured_media")
    started_at = time.time()

    def progress_hook(data: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise CaptureDownloadCancelled("Captured media download cancelled.")

        if on_progress is None:
            return

        status = str(data.get("status") or "")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            ratio = downloaded / total if total > 0 else 0.0
            speed = data.get("_speed_str") or data.get("speed") or ""
            eta = data.get("_eta_str") or data.get("eta")
            detail_parts = []
            if speed:
                detail_parts.append(str(speed).strip())
            if eta not in (None, ""):
                detail_parts.append(f"ETA {eta}")
            on_progress(max(0.0, min(1.0, ratio)), " • ".join(detail_parts))
        elif status == "finished":
            on_progress(1.0, "Finalizing media…")

    result: Any = None
    last_error: BaseException | None = None
    attempted_labels: list[str] = []

    for attempt, (transport_label, transport_options) in enumerate(capture_transport_profiles(), start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise CaptureDownloadCancelled("Captured media download cancelled.")

        attempted_labels.append(transport_label)
        if on_progress is not None:
            on_progress(0.0, f"{transport_label} • connecting")

        options = capture_download_options(
            item,
            output_dir,
            name,
            progress_hook,
            transport_options=transport_options,
        )

        try:
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(item["url"], download=True)
            last_error = None
            break
        except CaptureDownloadCancelled:
            raise
        except DownloadError as exc:
            last_error = exc
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise CaptureDownloadCancelled("Captured media download cancelled.") from exc
            last_error = exc

        cleanup_failed_capture_parts(output_dir, name, started_at)

        # For normal HTTP authorization/not-found errors, do not burn through
        # every network transport. Chrome impersonation has already had a
        # chance; let the outer candidate fallback try the next fresh stream.
        if last_error is not None and not _transport_error_hint(last_error):
            break

    if last_error is not None:
        safe = _safe_download_error(last_error, item)
        tried = " → ".join(attempted_labels)
        raise RuntimeError(f"{safe} | Network attempts: {tried}") from last_error

    if cancel_event is not None and cancel_event.is_set():
        raise CaptureDownloadCancelled("Captured media download cancelled.")

    possible: list[Path] = []
    if isinstance(result, dict):
        for key in ("filepath", "_filename"):
            value = result.get(key)
            if value:
                possible.append(Path(str(value)))
        requested = result.get("requested_downloads")
        if isinstance(requested, list):
            for entry in requested:
                if isinstance(entry, dict):
                    value = entry.get("filepath")
                    if value:
                        possible.append(Path(str(value)))

    for path in possible:
        if path.exists() and path.is_file():
            return path

    candidates = _candidate_paths(output_dir, name, started_at)
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)

    raise RuntimeError("The captured stream finished without producing a media file.")


__all__ = [
    "CaptureDownloadCancelled",
    "capture_host",
    "capture_media_mode",
    "capture_transport_profiles",
    "cleanup_failed_capture_parts",
    "download_captured_media",
    "safe_capture_name",
]
