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


# Keep platform-specific format selection in media_sources so future social
# platforms can reuse the same fallback logic without bloating app.py.
sources = SOURCES.read_text(encoding="utf-8")
if "def video_format_selector(" not in sources:
    sources += '''\n\ndef video_format_selector(url: str, quality: str) -> str:\n    \"\"\"Return a resilient yt-dlp video format selector.\n\n    Facebook and Instagram frequently expose only one combined Reel/video\n    format, or resolutions that do not exactly match the user's selected cap.\n    Prefer the requested quality where possible, then gracefully fall back to\n    the best combined MP4/combined stream before trying separate streams.\n    YouTube keeps the stricter quality-capped selector used previously.\n    \"\"\"\n    platform = detect_platform(url)\n    social = platform in {\"facebook\", \"instagram\"}\n\n    if quality == \"Best available\":\n        if social:\n            return \"b[ext=mp4]/b/bv*[ext=mp4]+ba[ext=m4a]/bv*+ba\"\n        return \"bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b\"\n\n    height = int(quality.rstrip(\"p\"))\n    if social:\n        return (\n            f\"b[height<={height}][ext=mp4]/\"\n            f\"b[height<={height}]/\"\n            \"b[ext=mp4]/b/\"\n            f\"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/\"\n            f\"bv*[height<={height}]+ba/\"\n            \"bv*[ext=mp4]+ba[ext=m4a]/bv*+ba\"\n        )\n\n    return (\n        f\"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/\"\n        f\"b[height<={height}][ext=mp4]/\"\n        f\"bv*[height<={height}]+ba/b[height<={height}]\"\n    )\n'''
SOURCES.write_text(sources, encoding="utf-8")

app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    "from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name, request_options\n",
    "from media_sources import browser_headers, detect_platform, is_supported_media_url, platform_name, request_options, video_format_selector\n",
    "media_sources import",
)
old_format_block = '''        else:\n            quality = self.video_quality_var.get()\n            if quality == \"Best available\":\n                opts[\"format\"] = \"bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b\"\n            else:\n                height = int(quality.rstrip(\"p\"))\n                opts[\"format\"] = (\n                    f\"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/\"\n                    f\"b[height<={height}][ext=mp4]/\"\n                    f\"bv*[height<={height}]+ba/b[height<={height}]\"\n                )\n            opts[\"merge_output_format\"] = \"mp4\"\n'''
new_format_block = '''        else:\n            quality = self.video_quality_var.get()\n            opts[\"format\"] = video_format_selector(url, quality)\n            opts[\"merge_output_format\"] = \"mp4\"\n'''
app = replace_once(app, old_format_block, new_format_block, "video format selection")
APP.write_text(app, encoding="utf-8")

# Extend tests to ensure a 720p social request can fall back to an uncapped best
# combined format instead of producing 'Requested format is not available'.
test = TEST.read_text(encoding="utf-8")
test = test.replace(
    "from media_sources import detect_platform, is_supported_media_url, platform_name, request_options\n",
    "from media_sources import detect_platform, is_supported_media_url, platform_name, request_options, video_format_selector\n",
)
if "test_social_video_format_fallback" not in test:
    insert = '''\n    def test_social_video_format_fallback(self):\n        instagram = video_format_selector(\"https://www.instagram.com/reel/ABC/\", \"720p\")\n        facebook = video_format_selector(\"https://www.facebook.com/reel/123\", \"720p\")\n        youtube = video_format_selector(\"https://youtu.be/abc\", \"720p\")\n        self.assertIn(\"b[ext=mp4]/b/\", instagram)\n        self.assertIn(\"b[ext=mp4]/b/\", facebook)\n        self.assertNotIn(\"b[ext=mp4]/b/\", youtube)\n\n    def test_social_best_available_selector(self):\n        selector = video_format_selector(\"https://www.instagram.com/reel/ABC/\", \"Best available\")\n        self.assertTrue(selector.startswith(\"b[ext=mp4]/b/\"))\n'''
    test = test.replace("\n\nif __name__ == \"__main__\":", insert + "\n\nif __name__ == \"__main__\":")
TEST.write_text(test, encoding="utf-8")

VERSION.write_text('APP_VERSION = "2.3.2"\n', encoding="utf-8")

readme = README.read_text(encoding="utf-8")
if "social format fallback" not in readme.lower():
    readme += "\n### Social format fallback\n\nFacebook and Instagram downloads prefer the selected quality, but if that exact resolution/stream is unavailable the app automatically falls back to the best compatible combined video or merged stream instead of failing with a requested-format error.\n"
README.write_text(readme, encoding="utf-8")

print("Applied Media Downloader v2.3.2 social format fallback hotfix.")
