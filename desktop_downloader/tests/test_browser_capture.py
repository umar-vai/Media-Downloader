from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_downloader"))

import app as app_module
import browser_extension
import startup
from media_core import options as engine_options


class StartupTests(unittest.TestCase):
    def test_command_starts_minimized(self) -> None:
        command = startup.launch_command()
        self.assertTrue(command.endswith(startup.MINIMIZED_FLAG))
        self.assertIn("launcher.py", command)  # running from source, not frozen

    def test_minimized_flag_detection(self) -> None:
        self.assertTrue(startup.started_minimized(["app.exe", "--minimized"]))
        self.assertFalse(startup.started_minimized(["app.exe"]))

    def test_setting_is_normalized(self) -> None:
        self.assertFalse(app_module.normalize_settings({})["start_with_windows"])
        self.assertTrue(app_module.normalize_settings({"start_with_windows": True})["start_with_windows"])
        self.assertFalse(app_module.normalize_settings({"start_with_windows": "yes"})["start_with_windows"])


class QualityTests(unittest.TestCase):
    def test_valid_qualities(self) -> None:
        for value in ("Best available", "1080p", "720p", "540p", "1440p", "2160p"):
            self.assertTrue(app_module.is_valid_quality(value), value)
        for value in ("", "best", "72p", "99999p", "720", "0720p"):
            self.assertFalse(app_module.is_valid_quality(value), value)


class HeaderForwardingTests(unittest.TestCase):
    def tearDown(self) -> None:
        engine_options.clear_request_context()

    def test_browser_headers_override_and_filter(self) -> None:
        engine_options.set_request_context(
            referer="https://embed.player/e/1",
            headers={"origin": "https://embed.player", "User-Agent": "UA/1", "Cookie": "x=1", "X-Bad": "a\r\nb"},
        )
        opts = engine_options.apply_request_options({"http_headers": {"User-Agent": "yt-dlp"}})
        headers = opts["http_headers"]
        self.assertEqual(headers["Referer"], "https://embed.player/e/1")
        self.assertEqual(headers["Origin"], "https://embed.player")
        self.assertEqual(headers["User-Agent"], "UA/1")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("X-Bad", headers)

    def test_header_values_with_newlines_are_dropped(self) -> None:
        engine_options.set_request_context(headers={"Origin": "https://a\r\nInjected: 1"})
        self.assertNotIn("http_headers", engine_options.apply_request_options({}))


class ExternalDownloadTests(unittest.TestCase):
    def _fake_app(self) -> SimpleNamespace:
        calls: list[tuple] = []
        fake = SimpleNamespace(
            calls=calls,
            tray=None,
            _default_batch_options=lambda mode=None: {
                "mode": "Video", "video_quality": "720p", "audio_format": "MP3", "audio_quality": "192",
            },
            _enqueue_batch_downloads=lambda text, options, extra=None: calls.append((text, options, extra)),
        )
        return fake

    def test_captured_stream_payload(self) -> None:
        fake = self._fake_app()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app_module, "EXT_COOKIE_DIR", Path(tmp)):
            app_module.DownloaderApp._handle_external_download(fake, {
                "url": "https://cdn.x/dub/master.m3u8",
                "referer": "https://embed.player/e/abc",
                "headers": {"Origin": "https://embed.player"},
                "title": "  Horror Collector   Episode 4 ",
                "quality": "1080p",
                "cookies": [{"domain": "cdn.x", "name": "a", "value": "1", "hostOnly": True}],
            })
        text, options, extra = fake.calls[0]
        self.assertEqual(text, "https://cdn.x/dub/master.m3u8")
        self.assertEqual(options["video_quality"], "1080p")
        self.assertEqual(extra["referer"], "https://embed.player/e/abc")
        self.assertEqual(extra["http_headers"], {"Origin": "https://embed.player"})
        self.assertEqual(extra["title_hint"], "Horror Collector Episode 4")
        self.assertTrue(extra["cookiefile"].endswith(".txt"))

    def test_eporner_cdn_playlist_downloads_the_page(self) -> None:
        fake = self._fake_app()
        stream = "https://dash-s2-c50-fr-cdn.eporner.com/hls/v1/1-,720p-av1,.mp4.urlset/master.m3u8?hash=a&expires=1"
        page = "https://www.eporner.com/video-KW9DXb6olC7/title/"
        app_module.DownloaderApp._handle_external_download(fake, {"url": stream, "pageUrl": page, "quality": "720p"})
        text, options, extra = fake.calls[0]
        self.assertEqual(text, page)
        self.assertEqual(extra["fallback_url"], stream)
        self.assertEqual(options["video_quality"], "720p")

    def test_bad_quality_falls_back_to_default(self) -> None:
        fake = self._fake_app()
        app_module.DownloaderApp._handle_external_download(fake, {"url": "https://e.com/v.mp4", "quality": "huge"})
        _text, options, extra = fake.calls[0]
        self.assertEqual(options["video_quality"], "720p")
        self.assertNotIn("title_hint", extra)


class ExtensionExportTests(unittest.TestCase):
    def test_export_writes_content_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = browser_extension.export_extension(Path(tmp) / "ext")
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["content_scripts"][0]["js"], ["content.js"])
            self.assertTrue(manifest["content_scripts"][0]["all_frames"])
            for name in ("background.js", "content.js", "popup.js", "options.js", "options.html"):
                self.assertTrue((folder / name).is_file(), name)
            self.assertIn("blocked", (folder / "options.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
