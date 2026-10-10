from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from media_core.engine import Cancelled, analyze_url, safe_filename


class MediaCoreEngineTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('A: bad / name?.mp4'), 'A_ bad _ name')

    @patch("media_core.engine.resolve_with_installed_browser")
    @patch("media_core.engine.extraction_attempts")
    def test_browser_fallback_is_used_after_extractor_failure(self, attempts, browser):
        attempts.return_value = []
        browser.return_value = {
            "title": "Fallback media",
            "formats": [{"url": "https://cdn.example/video.mp4", "height": 720}],
        }
        summary, info = analyze_url(
            "https://example.com/watch/1",
            cancel_event=threading.Event(),
        )
        self.assertEqual(summary["title"], "Fallback media")
        self.assertEqual(info["formats"][0]["height"], 720)

    def test_cancelled_analysis_stops_before_network(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            analyze_url("https://example.com/watch/1", cancel_event=event)


if __name__ == "__main__":
    unittest.main()
