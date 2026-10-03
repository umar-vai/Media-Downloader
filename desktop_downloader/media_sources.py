from __future__ import annotations

from urllib.parse import parse_qs, urlparse

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

    Facebook uses curl_cffi-backed Chrome impersonation because the standard
    Python TLS path can be terminated early on some networks. Instagram stays
    on yt-dlp's standard request path because curl_cffi/BoringSSL can itself be
    terminated by Instagram on some Windows/network combinations. YouTube also
    uses the standard path.
    """
    options = {"http_headers": browser_headers()}
    if detect_platform(url) == "facebook":
        options["impersonate"] = ImpersonateTarget("chrome")
    return options



def facebook_mobile_watch_url(url: str) -> str:
    """Convert common Facebook video/Reel links to the mobile watch endpoint.

    yt-dlp's own Facebook extractor uses m.facebook.com/watch for some Facebook
    URL forms. On networks where www.facebook.com terminates TLS early, the
    mobile watch endpoint can take a different edge/CDN path while preserving
    the same public video id.
    """
    if detect_platform(url) != "facebook":
        return url
    parsed = urlparse((url or "").strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if len(path_parts) >= 2 and path_parts[0].lower() == "reel" and path_parts[1].isdigit():
        video_id = path_parts[1]
    elif "videos" in [part.lower() for part in path_parts]:
        for part in reversed(path_parts):
            if part.isdigit():
                video_id = part
                break
    if not video_id:
        query = parse_qs(parsed.query)
        candidate = (query.get("v") or query.get("video_id") or [""])[0]
        if str(candidate).isdigit():
            video_id = str(candidate)
    if not video_id:
        return url
    return f"https://m.facebook.com/watch/?v={video_id}&_rdr"


def extraction_attempts(url: str) -> list[tuple[str, dict]]:
    """Return ordered extraction/network fallbacks for a media URL.

    Facebook is retried through the mobile watch endpoint, IPv4, and both
    standard yt-dlp TLS and Chrome/curl_cffi impersonation. Instagram and
    YouTube keep their known-working standard paths.
    """
    headers = {"http_headers": browser_headers()}
    if detect_platform(url) != "facebook":
        return [(url, request_options(url))]

    mobile_url = facebook_mobile_watch_url(url)
    candidates: list[tuple[str, dict]] = []
    seen: set[tuple[str, bool]] = set()
    for candidate_url in (mobile_url, url):
        for impersonate in (False, True):
            key = (candidate_url, impersonate)
            if key in seen:
                continue
            seen.add(key)
            options = {**headers, "source_address": "0.0.0.0"}
            if impersonate:
                options["impersonate"] = ImpersonateTarget("chrome")
            candidates.append((candidate_url, options))
    return candidates

def video_format_selector(url: str, quality: str) -> str:
    """Return a resilient yt-dlp video format selector.

    Facebook and Instagram frequently expose only one combined Reel/video
    format, or resolutions that do not exactly match the user's selected cap.
    Prefer the requested quality where possible, then gracefully fall back to
    the best combined MP4/combined stream before trying separate streams.
    YouTube keeps the stricter quality-capped selector used previously.
    """
    platform = detect_platform(url)
    social = platform in {"facebook", "instagram"}

    if quality == "Best available":
        if social:
            return "b[ext=mp4]/b/bv*[ext=mp4]+ba[ext=m4a]/bv*+ba"
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"

    height = int(quality.rstrip("p"))
    if social:
        return (
            f"b[height<={height}][ext=mp4]/"
            f"b[height<={height}]/"
            "b[ext=mp4]/b/"
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/"
            "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba"
        )

    return (
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={height}][ext=mp4]/"
        f"bv*[height<={height}]+ba/b[height<={height}]"
    )
