from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urlunparse

from yt_dlp.networking.impersonate import ImpersonateTarget

from network_proxy import yt_dlp_proxy_options

SUPPORTED_PLATFORMS = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
}


def _disable_instagram_auto_impersonation() -> None:
    """Keep Instagram off curl_cffi/BoringSSL on affected Windows networks.

    Recent yt-dlp Instagram extractors automatically enable browser
    impersonation whenever an impersonation-capable request handler is
    available. Media Downloader bundles curl_cffi for Facebook compatibility,
    so Instagram can otherwise select BoringSSL even when we do not request
    impersonation ourselves. The standard yt-dlp transport is more reliable on
    the tested network, so disable Instagram's automatic opt-in only.
    """
    try:
        from yt_dlp.extractor.instagram import InstagramBaseIE

        InstagramBaseIE._can_impersonate = False
    except Exception:
        # Do not make app startup dependent on yt-dlp's internal class layout.
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
    host = _hostname(url)
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        return "youtube"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com", "fb.watch", "www.fb.watch"}:
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

    Facebook uses curl_cffi-backed Chrome impersonation as one of its fallback
    transports. Instagram's extractor-level automatic impersonation is disabled
    above so Instagram stays on the standard yt-dlp request path. YouTube also
    uses the standard path.
    """
    options = {"http_headers": browser_headers(), **yt_dlp_proxy_options()}
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


def facebook_share_variants(url: str) -> list[str]:
    """Return alternate mobile hosts for Facebook share/short links.

    Facebook's /share/v/, /share/r/ and /share/p/ URLs are redirect-style links
    and are often handled by yt-dlp's generic extractor before they resolve to a
    canonical Facebook video/Reel URL. Some Windows/ISP combinations terminate
    TLS on www.facebook.com while the mobile/basic endpoints still resolve. Try
    those endpoints first and let yt-dlp follow the redirect to the final public
    media URL.
    """
    if detect_platform(url) != "facebook":
        return []
    parsed = urlparse((url or "").strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "share" or parts[1].lower() not in {"v", "r", "p"}:
        return []

    variants: list[str] = []
    for host in ("m.facebook.com", "mbasic.facebook.com"):
        variants.append(urlunparse((parsed.scheme or "https", host, parsed.path, parsed.params, parsed.query, parsed.fragment)))
    return variants


def extraction_attempts(url: str) -> list[tuple[str, dict]]:
    """Return ordered extraction/network fallbacks for a media URL.

    Facebook is retried through share-link mobile variants (when applicable),
    the mobile watch endpoint, IPv4, and both standard yt-dlp TLS and
    Chrome/curl_cffi impersonation. Instagram and YouTube keep their standard
    paths.
    """
    headers = {"http_headers": browser_headers(), **yt_dlp_proxy_options()}
    if detect_platform(url) != "facebook":
        return [(url, request_options(url))]

    mobile_url = facebook_mobile_watch_url(url)
    ordered_urls: list[str] = []
    for candidate_url in (*facebook_share_variants(url), mobile_url, url):
        if candidate_url not in ordered_urls:
            ordered_urls.append(candidate_url)

    candidates: list[tuple[str, dict]] = []
    seen: set[tuple[str, bool]] = set()
    for candidate_url in ordered_urls:
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
