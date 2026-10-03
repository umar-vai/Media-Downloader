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
    "from yt_dlp.networking.impersonate import ImpersonateTarget\n",
    "from yt_dlp.networking.impersonate import ImpersonateRequestHandler, ImpersonateTarget\n",
    "impersonate import",
)

if "def configure_ydl_transport(" not in sources:
    marker = "\ndef video_format_selector(url: str, quality: str) -> str:\n"
    helper = r'''

def configure_ydl_transport(ydl, url: str) -> None:
    """Apply platform-specific request-handler restrictions to a YoutubeDL instance.

    Instagram's extractor enables browser impersonation whenever an
    ImpersonateRequestHandler is available. On some Windows/ISP combinations
    curl_cffi/BoringSSL is terminated by Instagram even though the standard
    yt-dlp handlers work. For Instagram only, remove impersonation-capable
    handlers from this YoutubeDL instance before the extractor initializes.

    Facebook intentionally keeps curl_cffi because the v2.3.4 fallback chain
    uses it as one of several working connection paths.
    """
    if detect_platform(url) != "instagram":
        return

    director = ydl._request_director
    for key, handler in list(director.handlers.items()):
        if not isinstance(handler, ImpersonateRequestHandler):
            continue
        director.handlers.pop(key, None)
        try:
            handler.close()
        except Exception:
            pass
'''
    sources = replace_once(sources, marker, helper + marker, "video format selector marker")
SOURCES.write_text(sources, encoding="utf-8")


# --- app.py ----------------------------------------------------------------
app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    "from media_sources import browser_headers, detect_platform, extraction_attempts, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "from media_sources import browser_headers, configure_ydl_transport, detect_platform, extraction_attempts, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "media_sources import",
)

app = replace_once(
    app,
    ") as ydl:\n                        info = ydl.extract_info(attempt_url, download=False) or {}",
    ") as ydl:\n                        configure_ydl_transport(ydl, attempt_url)\n                        info = ydl.extract_info(attempt_url, download=False) or {}",
    "analyze transport configuration",
)
app = replace_once(
    app,
    "with yt_dlp.YoutubeDL(attempt_opts) as ydl:\n                        ydl.extract_info(attempt_url, download=True)",
    "with yt_dlp.YoutubeDL(attempt_opts) as ydl:\n                        configure_ydl_transport(ydl, attempt_url)\n                        ydl.extract_info(attempt_url, download=True)",
    "download transport configuration",
)
APP.write_text(app, encoding="utf-8")


# --- tests -----------------------------------------------------------------
test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    "from media_sources import detect_platform, extraction_attempts, facebook_mobile_watch_url, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "from media_sources import configure_ydl_transport, detect_platform, extraction_attempts, facebook_mobile_watch_url, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "test media_sources import",
)
if "test_instagram_removes_impersonation_handlers" not in test:
    # Add imports needed only for the transport-director test.
    test = replace_once(
        test,
        "import unittest\nfrom pathlib import Path\n",
        "import unittest\nfrom pathlib import Path\n\nfrom yt_dlp import YoutubeDL\nfrom yt_dlp.networking.impersonate import ImpersonateRequestHandler\n",
        "test yt-dlp imports",
    )
    insert = '''\n    def test_instagram_removes_impersonation_handlers(self):\n        ydl = YoutubeDL({\"quiet\": True})\n        try:\n            self.assertTrue(\n                any(isinstance(handler, ImpersonateRequestHandler) for handler in ydl._request_director.handlers.values())\n            )\n            configure_ydl_transport(ydl, \"https://www.instagram.com/reel/ABC/\")\n            self.assertFalse(\n                any(isinstance(handler, ImpersonateRequestHandler) for handler in ydl._request_director.handlers.values())\n            )\n        finally:\n            ydl.close()\n\n    def test_facebook_keeps_impersonation_handlers(self):\n        ydl = YoutubeDL({\"quiet\": True})\n        try:\n            configure_ydl_transport(ydl, \"https://www.facebook.com/reel/123\")\n            self.assertTrue(\n                any(isinstance(handler, ImpersonateRequestHandler) for handler in ydl._request_director.handlers.values())\n            )\n        finally:\n            ydl.close()\n'''
    test = test.replace("\n\nif __name__ == \"__main__\":", insert + "\n\nif __name__ == \"__main__\":")
TEST.write_text(test, encoding="utf-8")

VERSION.write_text('APP_VERSION = "2.3.5"\n', encoding="utf-8")

readme = README.read_text(encoding="utf-8")
if "instagram transport isolation" not in readme.lower():
    readme += '''\n### Instagram transport isolation\n\nInstagram analysis/download sessions explicitly disable impersonation-capable request handlers so the Instagram extractor cannot automatically switch to curl_cffi/BoringSSL on networks where that TLS path is terminated. Facebook keeps the v2.3.4 multi-path fallback, including curl_cffi where useful.\n'''
README.write_text(readme, encoding="utf-8")

print("Applied Media Downloader v2.3.5 Instagram transport isolation hotfix.")
