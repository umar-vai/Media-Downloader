from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop_downloader" / "app.py"
SOURCES = ROOT / "desktop_downloader" / "media_sources.py"
TEST = ROOT / "desktop_downloader" / "tests" / "test_media_sources.py"
VERSION = ROOT / "desktop_downloader" / "version.py"
README = ROOT / "desktop_downloader" / "README.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not find expected block: {label}")
    return text.replace(old, new, 1)


# --- media_sources.py -------------------------------------------------------
sources = SOURCES.read_text(encoding="utf-8")
sources = replace_once(
    sources,
    "from urllib.parse import urlparse\n",
    "from urllib.parse import parse_qs, urlparse\n",
    "urllib imports",
)

if "def facebook_mobile_watch_url(" not in sources:
    marker = "\ndef video_format_selector(url: str, quality: str) -> str:\n"
    helpers = r'''

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
'''
    sources = replace_once(sources, marker, helpers + marker, "video format selector marker")
SOURCES.write_text(sources, encoding="utf-8")


# --- app.py ----------------------------------------------------------------
app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    "from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "from media_sources import browser_headers, detect_platform, extraction_attempts, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "media_sources import",
)

# Clear stale previous-media data before a fresh analysis.
old_analyze_start = '''        self._set_busy(True)\n        self._set_status(f"Reading {platform_name(platform)} media information…", "working")\n        self.media_badge.configure(text=f"{platform_name(platform).upper()} • ANALYZING", fg_color="#162344", text_color=CYAN)\n        threading.Thread(target=self._analyze_media_worker, args=(url,), daemon=True).start()\n'''
new_analyze_start = '''        self._set_busy(True)\n        self.current_info = None\n        self.title_label.configure(text=f"Analyzing {platform_name(platform)} media…")\n        self.meta_label.configure(text="Trying compatible connection paths…")\n        self._apply_thumbnail(None)\n        self._set_status(f"Reading {platform_name(platform)} media information…", "working")\n        self.media_badge.configure(text=f"{platform_name(platform).upper()} • ANALYZING", fg_color="#162344", text_color=CYAN)\n        threading.Thread(target=self._analyze_media_worker, args=(url,), daemon=True).start()\n'''
app = replace_once(app, old_analyze_start, new_analyze_start, "analyze start")

old_analyze_worker = '''    def _analyze_media_worker(self, url: str) -> None:\n        try:\n            with yt_dlp.YoutubeDL(\n                {\n                    "quiet": True,\n                    "no_warnings": True,\n                    "skip_download": True,\n                    "noplaylist": True,\n                    "cachedir": False,\n                    "socket_timeout": 30,\n                    "retries": 4,\n                    "fragment_retries": 4,\n                    **request_options(url),\n                }\n            ) as ydl:\n                info = ydl.extract_info(url, download=False) or {}\n\n            thumb_bytes = None\n'''
new_analyze_worker = '''    def _analyze_media_worker(self, url: str) -> None:\n        try:\n            attempts = extraction_attempts(url)\n            info: dict[str, Any] = {}\n            last_error: Exception | None = None\n            for index, (attempt_url, network_options) in enumerate(attempts, start=1):\n                if len(attempts) > 1:\n                    self.events.put(("status", f"Facebook connection attempt {index}/{len(attempts)}…"))\n                try:\n                    with yt_dlp.YoutubeDL(\n                        {\n                            "quiet": True,\n                            "no_warnings": True,\n                            "skip_download": True,\n                            "noplaylist": True,\n                            "cachedir": False,\n                            "socket_timeout": 30,\n                            "retries": 2,\n                            "fragment_retries": 2,\n                            **network_options,\n                        }\n                    ) as ydl:\n                        info = ydl.extract_info(attempt_url, download=False) or {}\n                    if info:\n                        break\n                except Exception as exc:\n                    last_error = exc\n            if not info:\n                raise last_error or RuntimeError("No compatible Facebook connection path succeeded.")\n\n            thumb_bytes = None\n'''
app = replace_once(app, old_analyze_worker, new_analyze_worker, "analyze worker")

app = app.replace(
    'Could not read this media link. Public links work best. Facebook/Instagram use browser-compatible TLS; private or login-required content is not supported.',
    'Could not read this media link after trying the available connection paths. Public links work best; private or login-required content is not supported.',
)

# Build download options independently from network transport, then retry the
# same download through each Facebook connection path.
old_opts_network = '''            "fragment_retries": 4,\n            "concurrent_fragment_downloads": 4,\n            **request_options(url),\n            "progress_hooks": [hook],\n'''
new_opts_network = '''            "fragment_retries": 4,\n            "concurrent_fragment_downloads": 4,\n            "progress_hooks": [hook],\n'''
app = replace_once(app, old_opts_network, new_opts_network, "download network options")

old_download_try = '''        try:\n            with yt_dlp.YoutubeDL(opts) as ydl:\n                ydl.extract_info(url, download=True)\n            candidates = [\n'''
new_download_try = '''        try:\n            attempts = extraction_attempts(url)\n            last_error: Exception | None = None\n            downloaded = False\n            for index, (attempt_url, network_options) in enumerate(attempts, start=1):\n                if len(attempts) > 1:\n                    self.events.put(("status", f"Facebook download connection {index}/{len(attempts)}…"))\n                attempt_opts = {**opts, **network_options}\n                try:\n                    with yt_dlp.YoutubeDL(attempt_opts) as ydl:\n                        ydl.extract_info(attempt_url, download=True)\n                    downloaded = True\n                    break\n                except Exception as exc:\n                    last_error = exc\n                    for partial in download_dir.glob(f"{name}.*"):\n                        if partial.suffix.lower() in {".part", ".ytdl", ".temp", ".tmp"}:\n                            try:\n                                partial.unlink()\n                            except OSError:\n                                pass\n            if not downloaded:\n                raise last_error or RuntimeError("No compatible Facebook connection path succeeded.")\n            candidates = [\n'''
app = replace_once(app, old_download_try, new_download_try, "download retry block")
APP.write_text(app, encoding="utf-8")


# --- tests -----------------------------------------------------------------
test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    "from media_sources import detect_platform, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "from media_sources import detect_platform, extraction_attempts, facebook_mobile_watch_url, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "test imports",
)

# v2.3.3 test expected Instagram to use standard transport already; keep that
# assertion and add explicit Facebook fallback coverage.
if "test_facebook_connection_fallbacks" not in test:
    insert = '''\n    def test_facebook_mobile_watch_url(self):\n        self.assertEqual(\n            facebook_mobile_watch_url(\"https://www.facebook.com/reel/1067651965750372\"),\n            \"https://m.facebook.com/watch/?v=1067651965750372&_rdr\",\n        )\n\n    def test_facebook_connection_fallbacks(self):\n        attempts = extraction_attempts(\"https://www.facebook.com/reel/1067651965750372\")\n        self.assertGreaterEqual(len(attempts), 4)\n        self.assertTrue(attempts[0][0].startswith(\"https://m.facebook.com/watch/\"))\n        self.assertEqual(attempts[0][1].get(\"source_address\"), \"0.0.0.0\")\n        self.assertNotIn(\"impersonate\", attempts[0][1])\n        self.assertEqual(attempts[1][1][\"impersonate\"].client, \"chrome\")\n\n    def test_instagram_keeps_single_standard_path(self):\n        attempts = extraction_attempts(\"https://www.instagram.com/reel/ABC/\")\n        self.assertEqual(len(attempts), 1)\n        self.assertNotIn(\"impersonate\", attempts[0][1])\n'''
    test = test.replace("\n\nif __name__ == \"__main__\":", insert + "\n\nif __name__ == \"__main__\":")
TEST.write_text(test, encoding="utf-8")

VERSION.write_text('APP_VERSION = "2.3.4"\n', encoding="utf-8")

readme = README.read_text(encoding="utf-8")
if "facebook connection fallback" not in readme.lower():
    readme += '''\n### Facebook connection fallback\n\nFacebook public videos/Reels automatically retry through the mobile watch endpoint, IPv4, standard yt-dlp TLS, and Chrome/curl_cffi transport when a network terminates one Facebook TLS path early. Instagram keeps the standard transport that is more reliable on the tested Windows network.\n'''
README.write_text(readme, encoding="utf-8")

print("Applied Media Downloader v2.3.4 Facebook connection fallback hotfix.")
