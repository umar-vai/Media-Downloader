from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from browser_resolver import _format_from_response, _looks_like_media


class BrowserResolverTests(unittest.TestCase):
    def test_media_detection(self):
        self.assertTrue(_looks_like_media("https://cdn.example/video.mp4?token=1", "video/mp4"))
        self.assertTrue(_looks_like_media("https://cdn.example/master", "application/vnd.apple.mpegurl"))
        self.assertFalse(_looks_like_media("https://example.com/page", "text/html"))

    def test_format_from_direct_mp4(self):
        result = _format_from_response(
            "https://example.com/watch/1",
            {"url": "https://cdn.example/720.mp4", "mimeType": "video/mp4"},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["ext"], "mp4")
        self.assertEqual(result["http_headers"]["Referer"], "https://example.com/watch/1")

    def test_format_from_hls(self):
        result = _format_from_response(
            "https://example.com/watch/1",
            {"url": "https://cdn.example/master.m3u8", "mimeType": "application/x-mpegURL"},
        )
        self.assertEqual(result["protocol"], "m3u8_native")


if __name__ == "__main__":
    unittest.main()
