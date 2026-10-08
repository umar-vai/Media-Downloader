from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from browser_capture import sanitize_capture, sanitize_headers
from media_editor_engine import ffmpeg_exe
from network_proxy import yt_dlp_proxy_options

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


def capture_download_options(
    capture: dict[str, Any],
    output_dir: Path,
    base_name: str,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
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
        "concurrent_fragment_downloads": 6,
        "socket_timeout": 30,
        "http_headers": headers,
        **yt_dlp_proxy_options(),
        "outtmpl": str(output_dir / f"{name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_exe(),
        "merge_output_format": "mp4",
        "format": "bestvideo*+bestaudio/best",
    }
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

    options = capture_download_options(item, output_dir, name, progress_hook)

    try:
        with YoutubeDL(options) as ydl:
            result = ydl.extract_info(item["url"], download=True)
    except CaptureDownloadCancelled:
        raise
    except DownloadError as exc:
        raise RuntimeError(_safe_download_error(exc, item)) from exc
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise CaptureDownloadCancelled("Captured media download cancelled.") from exc
        raise RuntimeError(_safe_download_error(exc, item)) from exc

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
