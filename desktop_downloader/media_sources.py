from __future__ import annotations

from urllib.parse import urlparse

from yt_dlp.networking.impersonate import ImpersonateTarget

SUPPORTED_PLATFORMS = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
}


def _hostname(url: str) -> str:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    return (parsed.hostname or "").lower().rstrip(".")


def detect_platform(url: str) -> str | None:
    host = _hostname(url)
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        return "youtube"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch", "www.fb.watch"}:
        return "facebook"
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return "instagram"
    return None


def is_supported_media_url(url: str) -> bool:
    return detect_platform(url) is not None


def platform_name(url_or_platform: str) -> str:
    platform = url_or_platform if url_or_platform in SUPPORTED_PLATFORMS else detect_platform(url_or_platform)
    return SUPPORTED_PLATFORMS.get(platform or "", "Media")


def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }


def request_options(url: str) -> dict:
    """Return yt-dlp request options appropriate for the detected platform.

    Facebook and Instagram can use TLS/browser fingerprinting. curl_cffi-backed
    Chrome impersonation avoids relying on Python urllib's TLS handshake for
    those sites. YouTube keeps the standard request path.
    """
    options = {"http_headers": browser_headers()}
    if detect_platform(url) in {"facebook", "instagram"}:
        options["impersonate"] = ImpersonateTarget("chrome")
    return options
