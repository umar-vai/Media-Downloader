from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.extractor.eporner import EpornerIE

from .network import yt_dlp_proxy_options

PORNHUB_HOSTS = {
    "pornhub.com",
    "www.pornhub.com",
    "m.pornhub.com",
}

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


def _patch_eporner_https_api() -> None:
    """Force Eporner's JSON metadata endpoint to HTTPS.

    Upstream currently constructs the XHR endpoint with http://. On some
    Windows/network combinations that endpoint produces curl (52) empty replies
    or curl (35) TLS shutdowns after redirects. Rewriting only Eporner's JSON
    request to HTTPS keeps the site extractor intact while avoiding that path.
    """
    if getattr(EpornerIE, "_media_downloader_https_patch", False):
        return

    original_download_json = EpornerIE._download_json

    def patched_download_json(self, url_or_request, *args, **kwargs):
        if isinstance(url_or_request, str) and url_or_request.startswith("http://www.eporner.com/xhr/video/"):
            url_or_request = "https://" + url_or_request[len("http://"):]
        return original_download_json(self, url_or_request, *args, **kwargs)

    EpornerIE._download_json = patched_download_json
    EpornerIE._media_downloader_https_patch = True


_patch_eporner_https_api()


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


def is_pornhub_url(url: str) -> bool:
    return _hostname(url) in PORNHUB_HOSTS


def pornhub_mobile_url(url: str) -> str:
    if not is_pornhub_url(url):
        return url
    parsed = urlparse((url or "").strip())
    return urlunparse(
        (
            parsed.scheme or "https",
            "m.pornhub.com",
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _available_cookie_browsers() -> list[str]:
    """Return browser stores that plausibly exist on this computer.

    These are consumed directly by yt-dlp and are never copied into app state.
    """
    import os
    from pathlib import Path

    candidates: list[tuple[str, Path]] = []
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    home = Path.home()

    if local:
        local_path = Path(local)
        candidates.extend(
            [
                ("chrome", local_path / "Google/Chrome/User Data"),
                ("edge", local_path / "Microsoft/Edge/User Data"),
                ("brave", local_path / "BraveSoftware/Brave-Browser/User Data"),
            ]
        )
    if roaming:
        candidates.append(("firefox", Path(roaming) / "Mozilla/Firefox/Profiles"))

    candidates.extend(
        [
            ("chrome", home / ".config/google-chrome"),
            ("chromium", home / ".config/chromium"),
            ("firefox", home / ".mozilla/firefox"),
        ]
    )

    result: list[str] = []
    for name, path in candidates:
        if name not in result and path.exists():
            result.append(name)
    return result


def browser_session_attempts(url: str) -> list[tuple[str, dict]]:
    """Fallbacks that reuse cookies from an installed browser session.

    This runs only after anonymous extraction has failed. Cookie stores are read
    locally by yt-dlp and are never persisted into Local Core job/settings state.
    Known social platforms keep their dedicated extraction paths; generic web
    pages can use the browser session because embedded players commonly require
    consent/session cookies before exposing a stream.
    """
    platform = detect_platform(url)
    if platform != "web":
        return []

    base = {
        **_base_request_options(),
        "compat_opts": {"prefer-legacy-http-handler"},
        "source_address": "0.0.0.0",
    }
    attempts: list[tuple[str, dict]] = []
    for browser_name in _available_cookie_browsers():
        attempts.append(
            (
                url,
                {
                    **base,
                    "cookiesfrombrowser": (browser_name,),
                },
            )
        )
        attempts.append(
            (
                url,
                {
                    **base,
                    "cookiesfrombrowser": (browser_name,),
                    "_force_generic_extractor": True,
                },
            )
        )
    return attempts


def _generic_transport_attempts(url: str, *, allow_impersonation: bool = True) -> list[tuple[str, dict]]:
    """Prefer Python/legacy HTTP before curl_cffi for arbitrary websites."""
    base = _base_request_options()
    legacy = {
        **base,
        "compat_opts": {"prefer-legacy-http-handler"},
        "source_address": "0.0.0.0",
    }
    attempts: list[tuple[str, dict]] = [
        (url, dict(legacy)),
        (url, dict(base)),
        (url, {**legacy, "_force_generic_extractor": True}),
        (url, {**base, "_force_generic_extractor": True}),
    ]

    # curl_cffi impersonation is intentionally last. On some Windows/network
    # paths BoringSSL closes abruptly even when urllib/Chrome can reach the page.
    if allow_impersonation:
        attempts.extend(
            [
                (url, {**base, "impersonate": ImpersonateTarget("chrome")}),
                (
                    url,
                    {
                        **base,
                        "impersonate": ImpersonateTarget("chrome"),
                        "_force_generic_extractor": True,
                    },
                ),
            ]
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


_DIRECT_STREAM_RE = re.compile(
    r"\.(m3u8|mpd|mp4|webm|mkv|mov|m4v|m4a|mp3|ogg|opus|flac|wav)$", re.IGNORECASE
)


def is_direct_stream_url(url: str) -> bool:
    """True for a link to the media itself (an HLS/DASH playlist or a media file), not a page."""
    try:
        path = urlparse((url or "").strip()).path
    except ValueError:
        return False
    return bool(_hostname(url)) and bool(_DIRECT_STREAM_RE.search(path))


def _base_domain(host: str) -> str:
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "net", "org", "ac", "gov", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_DEDICATED_CACHE: dict[str, bool] = {}


def has_dedicated_extractor(url: str) -> bool:
    """True when yt-dlp has a site extractor (not just Generic) for this page."""
    host = _hostname(url)
    if not host:
        return False
    key = url.split("#", 1)[0]
    if key in _DEDICATED_CACHE:
        return _DEDICATED_CACHE[key]
    found = False
    try:
        from yt_dlp.extractor import gen_extractor_classes

        for extractor in gen_extractor_classes():
            name = getattr(extractor, "ie_key", lambda: "")()
            if name == "Generic":
                continue
            try:
                if extractor.suitable(key):
                    found = True
                    break
            except Exception:
                continue
    except Exception:
        found = False
    if len(_DEDICATED_CACHE) > 500:
        _DEDICATED_CACHE.clear()
    _DEDICATED_CACHE[key] = found
    return found


def preferred_page_url(stream_url: str, page_url: str) -> str | None:
    """The page link to download instead of a stream the browser captured, or None.

    Sites such as Eporner hand the player short-lived, IP-signed CDN playlists
    (``.../master.m3u8?hash=...&expires=...``). Downloading that captured link fails often:
    the CDN throttles a second client, then redirects it to a 404. yt-dlp's own extractor for
    the page asks the site for fresh links (Eporner: plain MP4 files), which is reliable.
    Only used when the stream lives on the same site as the page, so a page that merely
    embeds someone else's player keeps the stream the user picked."""
    if not is_direct_stream_url(stream_url) or is_direct_stream_url(page_url):
        return None
    stream_host, page_host = _hostname(stream_url), _hostname(page_url)
    if not stream_host or not page_host or _base_domain(stream_host) != _base_domain(page_host):
        return None
    return page_url if has_dedicated_extractor(page_url) else None


def page_fallback_url(stream_url: str, page_url: str) -> str | None:
    """The page link to try when a captured stream cannot be read, or None.

    A stream the browser captured is often short-lived or tied to the browser's session: a
    PornHub playlist on ``hv-h.phncdn.com`` answers "request incorrect" to any other client.
    The stream's CDN has a different domain from the page, so ``preferred_page_url`` keeps the
    stream; when it then fails, the page (which yt-dlp's site extractor reads fresh links from,
    like "Paste URL" does) is the way to still get the video."""
    if not is_direct_stream_url(stream_url) or is_direct_stream_url(page_url):
        return None
    if not _hostname(page_url) or page_url.split("#", 1)[0] == stream_url.split("#", 1)[0]:
        return None
    return page_url if has_dedicated_extractor(page_url) else None


def extraction_attempts(url: str) -> list[tuple[str, dict]]:
    """Return ordered extraction/network fallbacks for any HTTP(S) media page."""
    platform = detect_platform(url)
    if platform is None:
        return []

    host = _hostname(url)
    if platform == "web" and is_direct_stream_url(url):
        # A playlist or media file: there is no page to try in other ways. Two transports are
        # enough; six attempts on a dead or expired link only kept the user waiting minutes.
        base = _base_request_options()
        legacy = {**base, "compat_opts": {"prefer-legacy-http-handler"}, "source_address": "0.0.0.0"}
        return [
            (url, {**legacy, "_force_generic_extractor": True}),
            (url, {**base, "_force_generic_extractor": True}),
        ]
    if host in PORNHUB_HOSTS:
        base = _base_request_options()
        legacy = {
            **base,
            "compat_opts": {"prefer-legacy-http-handler"},
            "source_address": "0.0.0.0",
        }
        mobile = pornhub_mobile_url(url)
        attempts: list[tuple[str, dict]] = [(url, dict(legacy))]
        if mobile != url:
            attempts.append((mobile, dict(legacy)))
        attempts.append((url, {**legacy, "_force_generic_extractor": True}))
        if mobile != url:
            attempts.append((mobile, {**legacy, "_force_generic_extractor": True}))
        return attempts

    if host in {"eporner.com", "www.eporner.com"}:
        base = _base_request_options()
        embed = eporner_embed_url(url)

        # Prefer yt-dlp's legacy urllib handler for Eporner. The packaged Windows
        # build has repeatedly hit curl_cffi/SSL failures on the site's XHR
        # metadata endpoint after one successful download.
        legacy = {
            **base,
            "compat_opts": {"prefer-legacy-http-handler"},
            "source_address": "0.0.0.0",
        }

        attempts: list[tuple[str, dict]] = [(url, dict(legacy))]
        if embed and embed != url:
            attempts.append((embed, dict(legacy)))

        # If the site extractor still fails, force Generic extraction from both
        # page variants using the same non-curl transport.
        attempts.append((url, {**legacy, "_force_generic_extractor": True}))
        if embed and embed != url:
            attempts.append((embed, {**legacy, "_force_generic_extractor": True}))
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
        # The browser extension sends the tab's YouTube cookies with every lookup. A logged-in
        # session copied out of a running browser is rotated by YouTube and makes yt-dlp fail
        # ("could not read this video") while the same link works when pasted into the app.
        # So try the way the app does first (no per-request cookies), then with them, which
        # age-restricted or members-only videos need.
        anonymous = {**base, "_skip_request_cookies": True}
        return [
            (url, dict(anonymous)),
            (url, dict(base)),
            (url, {**base, "source_address": "0.0.0.0"}),
        ]

    return _generic_transport_attempts(url, allow_impersonation=True)


def has_separate_audio(info: Any) -> bool:
    """True when the analyzed media keeps audio in its own stream (HLS/DASH with an audio
    rendition). Picking a single "best" format there gives a silent video."""
    if not isinstance(info, dict):
        return False
    for item in info.get("formats") or []:
        if not isinstance(item, dict):
            continue
        vcodec = str(item.get("vcodec") or "")
        acodec = str(item.get("acodec") or "")
        if vcodec == "none" and acodec != "none":
            return True
        if not vcodec and str(item.get("format_id") or "").lower().startswith(("hls-audio", "audio", "aud")):
            return True
    return False


def video_format_selector(url: str, quality: str, info: Any = None) -> str:
    """Return a resilient format selector for known and generic websites."""
    platform = detect_platform(url)
    flexible = platform != "youtube"

    if flexible and has_separate_audio(info):
        # Video variants may not declare their (missing) audio codec, so "best single file"
        # would pick a silent variant: always merge the chosen video with the best audio.
        if quality == "Best available":
            return "bv*+ba/b/bv*"
        height = int(quality.rstrip("p"))
        return (
            f"bv*[height<=?{height}]+ba/"
            f"b[height<=?{height}]/"
            "bv*+ba/b/bv*"
        )

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
