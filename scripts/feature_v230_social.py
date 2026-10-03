from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop_downloader" / "app.py"
VERSION = ROOT / "desktop_downloader" / "version.py"
README = ROOT / "desktop_downloader" / "README.md"
SOURCES = ROOT / "desktop_downloader" / "media_sources.py"
TEST = ROOT / "desktop_downloader" / "tests" / "test_media_sources.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not find expected block: {label}")
    return text.replace(old, new, 1)


SOURCES.write_text('''from __future__ import annotations\n\nfrom urllib.parse import urlparse\n\nSUPPORTED_PLATFORMS = {\n    "youtube": "YouTube",\n    "facebook": "Facebook",\n    "instagram": "Instagram",\n}\n\n\ndef _hostname(url: str) -> str:\n    try:\n        parsed = urlparse((url or "").strip())\n    except ValueError:\n        return ""\n    if parsed.scheme.lower() not in {"http", "https"}:\n        return ""\n    return (parsed.hostname or "").lower().rstrip(".")\n\n\ndef detect_platform(url: str) -> str | None:\n    host = _hostname(url)\n    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:\n        return "youtube"\n    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch", "www.fb.watch"}:\n        return "facebook"\n    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:\n        return "instagram"\n    return None\n\n\ndef is_supported_media_url(url: str) -> bool:\n    return detect_platform(url) is not None\n\n\ndef platform_name(url_or_platform: str) -> str:\n    platform = url_or_platform if url_or_platform in SUPPORTED_PLATFORMS else detect_platform(url_or_platform)\n    return SUPPORTED_PLATFORMS.get(platform or "", "Media")\n\n\ndef browser_headers() -> dict[str, str]:\n    return {\n        "User-Agent": (\n            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "\n            "AppleWebKit/537.36 (KHTML, like Gecko) "\n            "Chrome/131.0.0.0 Safari/537.36"\n        ),\n        "Accept-Language": "en-US,en;q=0.9",\n    }\n''', encoding="utf-8")

TEST.write_text('''import unittest\n\nfrom media_sources import detect_platform, is_supported_media_url, platform_name\n\n\nclass MediaSourceTests(unittest.TestCase):\n    def test_youtube_urls(self):\n        for url in (\n            "https://www.youtube.com/watch?v=abc",\n            "https://youtu.be/abc",\n            "https://www.youtube.com/shorts/abc",\n        ):\n            self.assertEqual(detect_platform(url), "youtube")\n\n    def test_facebook_urls(self):\n        for url in (\n            "https://www.facebook.com/reel/123",\n            "https://www.facebook.com/watch/?v=123",\n            "https://fb.watch/abc/",\n        ):\n            self.assertEqual(detect_platform(url), "facebook")\n\n    def test_instagram_urls(self):\n        for url in (\n            "https://www.instagram.com/reel/ABC/",\n            "https://www.instagram.com/p/ABC/",\n        ):\n            self.assertEqual(detect_platform(url), "instagram")\n\n    def test_rejects_unsupported_or_non_http_urls(self):\n        for url in ("", "not-a-url", "ftp://youtube.com/a", "https://example.com/video"):\n            self.assertFalse(is_supported_media_url(url))\n\n    def test_platform_names(self):\n        self.assertEqual(platform_name("facebook"), "Facebook")\n        self.assertEqual(platform_name("https://instagram.com/reel/ABC/"), "Instagram")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

text = APP.read_text(encoding="utf-8")
text = replace_once(
    text,
    'from update_manager import ReleaseInfo, download_release, fetch_latest_release, is_newer_version\nfrom version import APP_VERSION\n',
    'from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name\nfrom update_manager import ReleaseInfo, download_release, fetch_latest_release, is_newer_version\nfrom version import APP_VERSION\n',
    "imports",
)
text = replace_once(
    text,
    'UPDATE_LOG_FILE = CONFIG_DIR / "update.log"\nYOUTUBE_RE = re.compile(r"^https?://(?:(?:www\\.|m\\.|music\\.)?youtube\\.com|youtu\\.be)/", re.I)\n',
    'UPDATE_LOG_FILE = CONFIG_DIR / "update.log"\n',
    "youtube regex",
)
text = text.replace('fallback: str = "youtube_download"', 'fallback: str = "media_download"')
text = text.replace('text="VIDEO • AUDIO"', 'text="YOUTUBE • FACEBOOK • INSTAGRAM"')
text = text.replace('text="Fast local downloads. Clean controls. Your connection, your files."', 'text="Download public videos and audio from YouTube, Facebook and Instagram."')
text = text.replace('self._section_title(card, "01 / Source", "Paste a YouTube link")', 'self._section_title(card, "01 / Source", "Paste a media link")')
text = text.replace('placeholder_text="https://www.youtube.com/watch?v=..."', 'placeholder_text="YouTube, Facebook or Instagram URL"')
text = text.replace('lambda _event: self.read_video()', 'lambda _event: self.analyze_media()')
text = text.replace('text="Analyze video"', 'text="Analyze media"')
text = text.replace('command=self.read_video,', 'command=self.analyze_media,')
text = text.replace('text="Analyze a video to see its details here"', 'text="Analyze media to see its details here"')
text = text.replace('text="Title, channel and duration will appear after analysis."', 'text="Title, creator, platform and duration will appear after analysis."')
text = text.replace('def read_video(self) -> None:', 'def analyze_media(self) -> None:')
text = text.replace('threading.Thread(target=self._read_video_worker, args=(url,), daemon=True).start()', 'threading.Thread(target=self._analyze_media_worker, args=(url,), daemon=True).start()')
text = text.replace('def _read_video_worker(self, url: str) -> None:', 'def _analyze_media_worker(self, url: str) -> None:')

text = replace_once(
    text,
    '''        url = self.url_var.get().strip()\n        if not YOUTUBE_RE.match(url):\n            messagebox.showerror(APP_NAME, "Please paste a valid YouTube or youtu.be URL.")\n            return\n        self._set_busy(True)\n        self._set_status("Reading video information…", "working")\n        self.media_badge.configure(text="ANALYZING", fg_color="#162344", text_color=CYAN)\n''',
    '''        url = self.url_var.get().strip()\n        platform = detect_platform(url)\n        if not platform:\n            messagebox.showerror(APP_NAME, "Please paste a valid YouTube, Facebook or Instagram URL.")\n            return\n        self._set_busy(True)\n        self._set_status(f"Reading {platform_name(platform)} media information…", "working")\n        self.media_badge.configure(text=f"{platform_name(platform).upper()} • ANALYZING", fg_color="#162344", text_color=CYAN)\n''',
    "analyze validation",
)

text = replace_once(
    text,
    '''                    "socket_timeout": 30,\n                    "retries": 2,\n''',
    '''                    "socket_timeout": 30,\n                    "retries": 4,\n                    "fragment_retries": 4,\n                    "http_headers": browser_headers(),\n''',
    "analyze yt-dlp options",
)

text = replace_once(
    text,
    '''                    {\n                        "title": str(info.get("title") or "YouTube video"),\n                        "channel": str(info.get("channel") or info.get("uploader") or "YouTube"),\n                        "duration": format_duration(info.get("duration")),\n                        "views": info.get("view_count"),\n                        "info": info,\n                        "thumbnail": thumb_bytes,\n                    },\n''',
    '''                    {\n                        "title": str(info.get("title") or info.get("description") or "Media"),\n                        "channel": str(info.get("channel") or info.get("uploader") or info.get("uploader_id") or "Creator"),\n                        "duration": format_duration(info.get("duration")),\n                        "views": info.get("view_count"),\n                        "platform": detect_platform(url) or str(info.get("extractor_key") or "media").lower(),\n                        "info": info,\n                        "thumbnail": thumb_bytes,\n                    },\n''',
    "info payload",
)
text = text.replace('self.events.put(("error", f"Could not read this YouTube link.\\n\\n{exc}"))', 'self.events.put(("error", f"Could not read this media link. Public links work best; private or login-required content is not supported.\\n\\n{exc}"))')

text = replace_once(
    text,
    '''        url = self.url_var.get().strip()\n        if not YOUTUBE_RE.match(url):\n            messagebox.showerror(APP_NAME, "Please paste a valid YouTube or youtu.be URL.")\n            return\n\n        self.download_dir.mkdir(parents=True, exist_ok=True)\n        name = safe_filename(self.name_var.get(), "youtube_download")\n''',
    '''        url = self.url_var.get().strip()\n        platform = detect_platform(url)\n        if not platform:\n            messagebox.showerror(APP_NAME, "Please paste a valid YouTube, Facebook or Instagram URL.")\n            return\n\n        self.download_dir.mkdir(parents=True, exist_ok=True)\n        name = safe_filename(self.name_var.get(), "media_download")\n''',
    "download validation",
)
text = text.replace('self._set_status("Connecting to YouTube…", "working")', 'self._set_status(f"Connecting to {platform_name(platform)}…", "working")')

text = replace_once(
    text,
    '''            "fragment_retries": 3,\n            "concurrent_fragment_downloads": 4,\n            "progress_hooks": [hook],\n''',
    '''            "fragment_retries": 4,\n            "concurrent_fragment_downloads": 4,\n            "http_headers": browser_headers(),\n            "progress_hooks": [hook],\n''',
    "download yt-dlp options",
)
text = text.replace('"format": "bestaudio[ext=m4a]/bestaudio/best",', '"format": "bestaudio/best",')

text = replace_once(
    text,
    '''                    self.meta_label.configure(text=f"{payload['channel']}  •  {payload['duration']}")\n                    self.media_badge.configure(text="VIDEO READY", fg_color="#0E3025", text_color=SUCCESS)\n                    self._apply_thumbnail(payload.get("thumbnail"))\n                    self._set_status("Video information loaded", "ready")\n''',
    '''                    platform_label = platform_name(str(payload.get("platform") or ""))\n                    self.meta_label.configure(text=f"{payload['channel']}  •  {platform_label}  •  {payload['duration']}")\n                    self.media_badge.configure(text=f"{platform_label.upper()} • READY", fg_color="#0E3025", text_color=SUCCESS)\n                    self._apply_thumbnail(payload.get("thumbnail"))\n                    self._set_status(f"{platform_label} media information loaded", "ready")\n''',
    "info ui",
)
text = text.replace('self.title_label.configure(text="Analyze a video to see its details here")', 'self.title_label.configure(text="Analyze media to see its details here")')
text = text.replace('self.meta_label.configure(text="Title, channel and duration will appear after analysis.")', 'self.meta_label.configure(text="Title, creator, platform and duration will appear after analysis.")')
APP.write_text(text, encoding="utf-8")

VERSION.write_text('APP_VERSION = "2.3.0"\n', encoding="utf-8")

readme = README.read_text(encoding="utf-8")
readme = readme.replace(
    "A portable Windows desktop media downloader. By default, downloaded media is saved to:",
    "A portable Windows desktop media downloader for public YouTube, Facebook and Instagram videos/reels. By default, downloaded media is saved to:",
)
if "## Supported platforms" not in readme:
    marker = "## Auto-update system\n"
    block = """## Supported platforms\n\n- YouTube videos and Shorts\n- Facebook public videos and Reels\n- Instagram public videos and Reels\n\nThe app automatically detects the platform from the pasted URL. Private, friends-only, login-required, DRM-protected or otherwise access-restricted media is not bypassed. Platform changes can occasionally require a newer `yt-dlp` release.\n\n"""
    readme = readme.replace(marker, block + marker)
README.write_text(readme, encoding="utf-8")

print("Applied Media Downloader v2.3.0 social platform support.")
