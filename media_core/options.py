"""Process-wide download options (cookies, subtitles, metadata, speed) applied by the engine.

The desktop app calls :func:`configure` whenever settings change; the engine reads the
values through the helper functions below. Everything defaults to "off"/neutral so the
engine behaves exactly as before when nothing is configured.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LOCAL = threading.local()
_OPTIONS: dict[str, Any] = {}

AUDIO_CODECS = {"mp3": "mp3", "m4a": "m4a", "flac": "flac", "opus": "opus", "wav": "wav"}
MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts",
    ".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg", ".wav",
}
SIDECAR_SUFFIXES = {".srt", ".vtt", ".ass", ".lrc", ".jpg", ".jpeg", ".png", ".webp", ".json", ".description"}
_BROWSERS = {"firefox": "firefox", "chrome": "chrome", "edge": "edge", "brave": "brave"}


def configure(**values: Any) -> None:
    with _LOCK:
        _OPTIONS.update(values)


def get(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _OPTIONS.get(key, default)


def reset() -> None:
    with _LOCK:
        _OPTIONS.clear()


def set_request_context(*, cookiefile: str = "", referer: str = "") -> None:
    """Per-thread overrides (cookies/referer sent by the browser extension for one download)."""
    _LOCAL.cookiefile = str(cookiefile or "")
    _LOCAL.referer = str(referer or "")


def clear_request_context() -> None:
    set_request_context()


def apply_request_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Inject the user's cookies and the request referer into a yt-dlp option dict."""
    for key, value in cookie_options().items():
        opts.setdefault(key, value)
    referer = getattr(_LOCAL, "referer", "")
    if referer:
        headers = dict(opts.get("http_headers") or {})
        headers.setdefault("Referer", referer)
        opts["http_headers"] = headers
    return opts


def cookie_options() -> dict[str, Any]:
    """yt-dlp options that attach the user's own cookies to every request."""
    result: dict[str, Any] = {}
    request_cookies = getattr(_LOCAL, "cookiefile", "")
    if request_cookies and Path(request_cookies).is_file():
        return {"cookiefile": request_cookies}
    cookie_file = str(get("cookies_file", "") or "").strip()
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if path.is_file():
            result["cookiefile"] = str(path)
    browser = _BROWSERS.get(str(get("cookies_browser", "") or "").strip().lower())
    if browser and "cookiefile" not in result:
        result["cookiesfrombrowser"] = (browser,)
    return result


def download_extras(mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extra yt-dlp options and post-processors for subtitles, thumbnails and metadata."""
    opts: dict[str, Any] = {}
    processors: list[dict[str, Any]] = []
    is_audio = str(mode or "").lower() == "audio"

    if get("fast_mode", True):
        opts["concurrent_fragment_downloads"] = 16

    subtitles = str(get("subtitles", "Off") or "Off")
    if subtitles in {"Sidecar file", "Embed in video"} and not is_audio:
        langs = [part.strip() for part in str(get("subtitle_langs", "en") or "en").replace(";", ",").split(",")]
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = [lang for lang in langs if lang] or ["en"]
        opts["subtitlesformat"] = "srt/vtt/best"
        if subtitles == "Embed in video":
            processors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    if get("embed_thumbnail", False):
        opts["writethumbnail"] = True
        processors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        processors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    if get("embed_metadata", True):
        processors.insert(0, {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})

    return opts, processors


def is_final_media(path: Path) -> bool:
    """True for a finished media file (not a partial download, thumbnail or subtitle)."""
    suffix = path.suffix.lower()
    if suffix in SIDECAR_SUFFIXES or suffix in {".part", ".ytdl", ".tmp", ".temp"}:
        return False
    return True
