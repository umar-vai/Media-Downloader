from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop_downloader" / "app.py"
SOURCES = ROOT / "desktop_downloader" / "media_sources.py"
REQ = ROOT / "desktop_downloader" / "requirements.txt"
VERSION = ROOT / "desktop_downloader" / "version.py"
TEST = ROOT / "desktop_downloader" / "tests" / "test_media_sources.py"
README = ROOT / "desktop_downloader" / "README.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not find expected block: {label}")
    return text.replace(old, new, 1)


# Add browser impersonation request options for social platforms.
sources = SOURCES.read_text(encoding="utf-8")
sources = replace_once(
    sources,
    "from urllib.parse import urlparse\n",
    "from urllib.parse import urlparse\n\nfrom yt_dlp.networking.impersonate import ImpersonateTarget\n",
    "media_sources import",
)
if "def request_options(" not in sources:
    sources += '''\n\ndef request_options(url: str) -> dict:\n    \"\"\"Return yt-dlp request options appropriate for the detected platform.\n\n    Facebook and Instagram can use TLS/browser fingerprinting. curl_cffi-backed\n    Chrome impersonation avoids relying on Python urllib's TLS handshake for\n    those sites. YouTube keeps the standard request path.\n    \"\"\"\n    options = {\"http_headers\": browser_headers()}\n    if detect_platform(url) in {\"facebook\", \"instagram\"}:\n        options[\"impersonate\"] = ImpersonateTarget(\"chrome\")\n    return options\n'''
SOURCES.write_text(sources, encoding="utf-8")

app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    "from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name\n",
    "from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name, request_options\n",
    "app media_sources import",
)
# Both analyze and download option dictionaries currently use the same static headers.
count = app.count('"http_headers": browser_headers(),')
if count != 2:
    raise RuntimeError(f"Expected 2 browser header option entries, found {count}")
app = app.replace('"http_headers": browser_headers(),', '**request_options(url),')
# Make the social-network error actionable without exposing a misleading private-content-only diagnosis.
app = app.replace(
    'Could not read this media link. Public links work best; private or login-required content is not supported.',
    'Could not read this media link. Public links work best. Facebook/Instagram use browser-compatible TLS; private or login-required content is not supported.',
)
APP.write_text(app, encoding="utf-8")

# Install yt-dlp's recommended browser impersonation backend.
req = REQ.read_text(encoding="utf-8")
req = req.replace("yt-dlp>=2026.8.19,<2027.0", "yt-dlp[default,curl-cffi]>=2026.8.19,<2027.0")
REQ.write_text(req, encoding="utf-8")

VERSION.write_text('APP_VERSION = "2.3.1"\n', encoding="utf-8")

# Extend tests to verify social URLs opt into Chrome impersonation while YouTube does not.
test = TEST.read_text(encoding="utf-8")
test = test.replace(
    "from media_sources import detect_platform, is_supported_media_url, platform_name\n",
    "from media_sources import detect_platform, is_supported_media_url, platform_name, request_options\n",
)
if "test_social_request_options" not in test:
    insert = '''\n    def test_social_request_options(self):\n        facebook = request_options(\"https://www.facebook.com/reel/123\")\n        instagram = request_options(\"https://www.instagram.com/reel/ABC/\")\n        youtube = request_options(\"https://youtu.be/abc\")\n        self.assertEqual(facebook[\"impersonate\"].client, \"chrome\")\n        self.assertEqual(instagram[\"impersonate\"].client, \"chrome\")\n        self.assertNotIn(\"impersonate\", youtube)\n'''
    test = test.replace("\n\nif __name__ == \"__main__\":", insert + "\n\nif __name__ == \"__main__\":")
TEST.write_text(test, encoding="utf-8")

readme = README.read_text(encoding="utf-8")
if "browser impersonation" not in readme.lower():
    marker = "The app automatically detects the platform from the pasted URL."
    replacement = (
        marker
        + " Facebook and Instagram requests use yt-dlp browser impersonation via curl_cffi for more reliable TLS/network compatibility."
    )
    readme = readme.replace(marker, replacement)
README.write_text(readme, encoding="utf-8")

print("Applied Media Downloader v2.3.1 social TLS hotfix.")
