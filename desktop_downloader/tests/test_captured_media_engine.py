from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from captured_media_engine import (
    _transport_error_hint,
    capture_download_options,
    capture_host,
    capture_media_mode,
    capture_transport_profiles,
    safe_capture_name,
)


class CapturedMediaEngineTests(unittest.TestCase):
    def test_safe_capture_name_removes_windows_invalid_characters(self) -> None:
        self.assertEqual(safe_capture_name('Episode: 1 / "Test"?'), "Episode_ 1 _ _Test_")

    def test_capture_media_mode_detects_audio(self) -> None:
        self.assertEqual(
            capture_media_mode(
                {
                    "url": "https://cdn.example/audio.bin",
                    "content_type": "audio/mp4",
                }
            ),
            "Audio",
        )
        self.assertEqual(
            capture_media_mode(
                {
                    "url": "https://cdn.example/movie.mp4",
                    "content_type": "video/mp4",
                }
            ),
            "Video",
        )

    def test_capture_host_can_prefer_page_host(self) -> None:
        capture = {
            "url": "https://cdn.example/video.mp4?token=secret",
            "page_url": "https://watch.example/episode/1",
        }
        self.assertEqual(capture_host(capture), "cdn.example")
        self.assertEqual(capture_host(capture, prefer_page=True), "watch.example")

    def test_transport_profiles_prefer_proxy_chrome_then_tun_fallback(self) -> None:
        with patch("captured_media_engine.active_proxy_url", return_value="http://127.0.0.1:7890"):
            profiles = capture_transport_profiles()
        labels = [label for label, _options in profiles]
        self.assertEqual(labels[0], "Proxy • Chrome TLS")
        self.assertIn("TUN/direct • Chrome TLS", labels)
        self.assertEqual(profiles[0][1]["proxy"], "http://127.0.0.1:7890")
        direct = next(options for label, options in profiles if label == "TUN/direct • Chrome TLS")
        self.assertEqual(direct["proxy"], "")

    def test_tls_eof_is_classified_as_transport_error(self) -> None:
        self.assertTrue(
            _transport_error_hint(
                RuntimeError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
            )
        )
        self.assertFalse(_transport_error_hint(RuntimeError("HTTP Error 403: Forbidden")))

    def test_options_can_override_transport_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            options = capture_download_options(
                {
                    "url": "https://cdn.example/master.m3u8",
                    "title": "Episode",
                },
                Path(directory),
                "Episode",
                transport_options={
                    "proxy": "",
                    "source_address": "0.0.0.0",
                    "concurrent_fragment_downloads": 1,
                },
            )
        self.assertEqual(options["proxy"], "")
        self.assertEqual(options["source_address"], "0.0.0.0")
        self.assertEqual(options["concurrent_fragment_downloads"], 1)

    def test_options_include_capture_headers_and_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            options = capture_download_options(
                {
                    "url": "https://cdn.example/master.m3u8",
                    "title": "Episode",
                    "headers": {
                        "Referer": "https://watch.example/episode",
                        "Cookie": "session=abc",
                        "X-Ignore": "no",
                    },
                },
                Path(directory),
                "Episode",
            )
        self.assertEqual(options["http_headers"]["Referer"], "https://watch.example/episode")
        self.assertEqual(options["http_headers"]["Cookie"], "session=abc")
        self.assertNotIn("X-Ignore", options["http_headers"])
        self.assertTrue(options["ffmpeg_location"])
        self.assertEqual(options["merge_output_format"], "mp4")


if __name__ == "__main__":
    unittest.main()
