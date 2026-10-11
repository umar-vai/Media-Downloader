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
    def test_pornhub_browser_session_fallbacks_include_site_and_generic_paths(self, _browsers):
        url = "https://www.pornhub.com/view_video.php?viewkey=abc123"
        attempts = browser_session_attempts(url)
        self.assertEqual(
            [opts["cookiesfrombrowser"] for _url, opts in attempts],
            [("chrome",), ("chrome",), ("firefox",), ("firefox",)],
        )
        self.assertFalse(attempts[0][1].get("_force_generic_extractor", False))
        self.assertTrue(attempts[1][1].get("_force_generic_extractor", False))
        self.assertTrue(all(_url == url for _url, _opts in attempts))

    @patch("media_core.sources._available_cookie_browsers", return_value=["edge"])
    def test_generic_web_pages_can_reuse_browser_session_after_anonymous_failure(self, _browsers):
        url = "https://anime.example/watch/episode-2"
        attempts = browser_session_attempts(url)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0][1]["cookiesfrombrowser"], ("edge",))
        self.assertTrue(attempts[1][1]["_force_generic_extractor"])

    def test_generic_transport_prefers_legacy_before_curl_impersonation(self):
        url = "https://anime.example/watch/episode-2"
        attempts = extraction_attempts(url)
        self.assertIn("prefer-legacy-http-handler", attempts[0][1].get("compat_opts", set()))
        self.assertNotIn("impersonate", attempts[0][1])
        impersonated_indexes = [
            index for index, (_candidate, options) in enumerate(attempts)
            if "impersonate" in options
        ]
        self.assertTrue(impersonated_indexes)
        self.assertGreater(min(impersonated_indexes), 2)

    def test_known_social_sites_do_not_read_browser_sessions_automatically(self):
        self.assertEqual(browser_session_attempts("https://www.youtube.com/watch?v=abc123"), [])


if __name__ == "__main__":
    unittest.main()
