from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from yt_dlp.networking.impersonate import ImpersonateTarget

from network_proxy import yt_dlp_proxy_options

SUPPORTED_PLATFORMS = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "web": "Website",
}


def _disable_instagram_auto_impersonation() -> None:
    """Keep Instagram off automatic curl_cffi/BoringSSL impersonation."""
    try:
        from yt_dlp.extractor.instagram import InstagramBaseIE

        InstagramBaseIE._can_impersonate = False
    except Exception:
        pass


_disable_instagram_auto_impersonation()


def _hostname(url: str) -> str:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    return (parsed.hostname or "").lower().rstrip(".")


def detect_platform(url: str) -> str | None:
    """Classify known sites but accept any valid HTTP(S) website as 'web'."""
    host = _hostname(url)
    if not host:
        return None
    if host in {
        "youtu.be",
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        return "youtube"
    if host in {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "mbasic.facebook.com",
        "web.facebook.com",
        "fb.watch",
        "www.fb.watch",
    }:
        return "facebook"
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return "instagram"
    return "web"


def is_supported_media_url(url: str) -> bool:
    return detect_platform(url) is not None


def platform_name(url_or_platform: str) -> str:
    platform = url_or_platform if url_or_platform in SUPPORTED_PLATFORMS else detect_platform(url_or_platform)
    if platform == "web":
        host = _hostname(url_or_platform)
        if host:
            return host.removeprefix("www.")
    return SUPPORTED_PLATFORMS.get(platform or "", "Website")


def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/155.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }


def _base_request_options() -> dict:
    return {"http_headers": browser_headers(), **yt_dlp_proxy_options()}


def request_options(url: str) -> dict:
    """Return a conservative first-choice yt-dlp transport."""
    options = _base_request_options()
    if detect_platform(url) == "facebook":
        options["impersonate"] = ImpersonateTarget("chrome")
    return options


def facebook_mobile_watch_url(url: str) -> str:
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


def facebook_share_variants(url: str) -> list[str]:
    if detect_platform(url) != "facebook":
        return []
    parsed = urlparse((url or "").strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "share" or parts[1].lower() not in {"v", "r", "p"}:
        return []

    variants: list[str] = []
    for host in ("m.facebook.com", "mbasic.facebook.com"):
        variants.append(
            urlunparse(
                (
                    parsed.scheme or "https",
                    host,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
        )
    return variants


def _generic_transport_attempts(url: str, *, allow_impersonation: bool = True) -> list[tuple[str, dict]]:
    """Try normal yt-dlp first, then browser-like and generic-page fallbacks."""
    base = _base_request_options()
    attempts: list[tuple[str, dict]] = [(url, dict(base))]

    if allow_impersonation:
        attempts.append((url, {**base, "impersonate": ImpersonateTarget("chrome")}))

    attempts.append((url, {**base, "_force_generic_extractor": True}))
    if allow_impersonation:
        attempts.append(
            (
                url,
                {
                    **base,
                    "impersonate": ImpersonateTarget("chrome"),
                    "_force_generic_extractor": True,
                },
            )
        )
    return attempts


def eporner_embed_url(url: str) -> str | None:
    """Return the canonical embed URL for an Eporner video URL."""
    host = _hostname(url)
    if host not in {"eporner.com", "www.eporner.com"}:
        return None
    try:
        path = urlparse((url or "").strip()).path
    except ValueError:
        return None
    match = re.search(r"/(?:video-|hd-porn/|embed/)([A-Za-z0-9]+)", path)
    if not match:
        return None
    return f"https://www.eporner.com/embed/{match.group(1)}/"


def extraction_attempts(url: str) -> list[tuple[str, dict]]:
    """Return ordered extraction/network fallbacks for any HTTP(S) media page."""
    platform = detect_platform(url)
    if platform is None:
        return []

    host = _hostname(url)
    if host in {"eporner.com", "www.eporner.com"}:
        base = _base_request_options()
        embed = eporner_embed_url(url)
        attempts: list[tuple[str, dict]] = [(url, dict(base))]
        if embed and embed != url:
            attempts.append((embed, dict(base)))

        # The current Eporner extractor can fail while fetching its JSON API.
        # A true Generic extractor pass can still recover direct media URLs from
        # the page/embed HTML. Do not use curl_cffi impersonation for this site:
        # the Windows build has shown curl (52)/(35) SSL transport failures here.
        attempts.append((url, {**base, "_force_generic_extractor": True}))
        if embed and embed != url:
            attempts.append((embed, {**base, "_force_generic_extractor": True}))
        return attempts

    if platform == "facebook":
        base = _base_request_options()
        mobile_url = facebook_mobile_watch_url(url)
        ordered_urls: list[str] = []
        for candidate_url in (*facebook_share_variants(url), mobile_url, url):
            if candidate_url not in ordered_urls:
                ordered_urls.append(candidate_url)

        candidates: list[tuple[str, dict]] = []
        for candidate_url in ordered_urls:
            candidates.append((candidate_url, {**base, "source_address": "0.0.0.0"}))
            candidates.append(
                (
                    candidate_url,
                    {
                        **base,
                        "source_address": "0.0.0.0",
                        "impersonate": ImpersonateTarget("chrome"),
                    },
                )
            )
        candidates.append((url, {**base, "_force_generic_extractor": True}))
        return candidates

    if platform == "instagram":
        return _generic_transport_attempts(url, allow_impersonation=False)

    if platform == "youtube":
        base = _base_request_options()
        return [
            (url, dict(base)),
            (url, {**base, "source_address": "0.0.0.0"}),
        ]

    return _generic_transport_attempts(url, allow_impersonation=True)


def video_format_selector(url: str, quality: str) -> str:
    """Return a resilient format selector for known and generic websites."""
    platform = detect_platform(url)
    flexible = platform != "youtube"

    if quality == "Best available":
        if flexible:
            return "b[ext=mp4]/b/bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/bv+ba"
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"

    height = int(quality.rstrip("p"))
    if flexible:
        return (
            f"b[height<={height}][ext=mp4]/"
            f"b[height<={height}]/"
            "b[ext=mp4]/b/"
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/"
            "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/bv+ba"
        )

    return (
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={height}][ext=mp4]/"
        f"bv*[height<={height}]+ba/b[height<={height}]"
    )
