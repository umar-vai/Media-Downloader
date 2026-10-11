from __future__ import annotations

import unittest
from unittest.mock import patch

from media_core.sources import (
    browser_session_attempts,
    extraction_attempts,
    is_pornhub_url,
    pornhub_mobile_url,
)


class SourceFallbackTests(unittest.TestCase):
    def test_pornhub_prefers_non_curl_transport(self):
        url = "https://www.pornhub.com/view_video.php?viewkey=abc123"
        self.assertTrue(is_pornhub_url(url))
        self.assertEqual(
            pornhub_mobile_url(url),
            "https://m.pornhub.com/view_video.php?viewkey=abc123",
        )

        attempts = extraction_attempts(url)
        urls = [candidate for candidate, _options in attempts]
        self.assertIn(url, urls)
        self.assertIn("https://m.pornhub.com/view_video.php?viewkey=abc123", urls)
        self.assertTrue(all("impersonate" not in options for _candidate, options in attempts))
        self.assertTrue(
            all("prefer-legacy-http-handler" in options.get("compat_opts", set()) for _candidate, options in attempts)
        )

    @patch("media_core.sources._available_cookie_browsers", return_value=["chrome", "firefox"])
    def test_pornhub_browser_session_fallbacks_are_local_and_ordered(self, _browsers):
        url = "https://www.pornhub.com/view_video.php?viewkey=abc123"
        attempts = browser_session_attempts(url)
        self.assertEqual([opts["cookiesfrombrowser"] for _url, opts in attempts], [("chrome",), ("firefox",)])
        self.assertTrue(all(_url == url for _url, _opts in attempts))

    def test_other_sites_do_not_read_browser_sessions_automatically(self):
        self.assertEqual(browser_session_attempts("https://example.com/video"), [])


if __name__ == "__main__":
    unittest.main()
