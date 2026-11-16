from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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

    def test_youtube_tries_without_extension_cookies_first(self):
        from media_core import options as engine_options

        url = "https://www.youtube.com/watch?v=abc123&list=RDabc123&start_radio=1"
        attempts = extraction_attempts(url)
        self.assertGreaterEqual(len(attempts), 2)
        self.assertTrue(attempts[0][1].get("_skip_request_cookies"))
        self.assertFalse(attempts[1][1].get("_skip_request_cookies"))

        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / "cookies.txt"
            jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            engine_options.set_request_context(cookiefile=str(jar))
            try:
                first = engine_options.apply_request_options(dict(attempts[0][1]))
                second = engine_options.apply_request_options(dict(attempts[1][1]))
            finally:
                engine_options.clear_request_context()
        self.assertNotIn("_skip_request_cookies", first)
        self.assertNotEqual(first.get("cookiefile"), str(jar))
        self.assertEqual(second.get("cookiefile"), str(jar))


if __name__ == "__main__":
    unittest.main()


class CapturedStreamTests(unittest.TestCase):
    EPORNER_HLS = (
        "https://dash-s2-c50-fr-cdn.eporner.com/hls/v1/17435191-,240p-av1,720p-av1,.mp4.urlset/"
        "master.m3u8?hash=abc&expires=1794817459&ip=1.2.3.4"
    )
    EPORNER_PAGE = "https://www.eporner.com/video-KW9DXb6olC7/some-title/"

    def test_site_cdn_playlist_prefers_the_page(self):
        from media_core.sources import preferred_page_url

        self.assertEqual(preferred_page_url(self.EPORNER_HLS, self.EPORNER_PAGE), self.EPORNER_PAGE)

    def test_other_sites_player_keeps_the_stream(self):
        from media_core.sources import preferred_page_url

        # A page that embeds another site's player, or a site yt-dlp has no extractor for.
        self.assertIsNone(preferred_page_url("https://cdn.player.net/v/master.m3u8", self.EPORNER_PAGE))
        self.assertIsNone(
            preferred_page_url("https://cdn.unknown-site.example/a/master.m3u8", "https://unknown-site.example/watch/1")
        )
        self.assertIsNone(preferred_page_url(self.EPORNER_PAGE, self.EPORNER_PAGE))

    def test_direct_stream_has_few_attempts(self):
        attempts = extraction_attempts(self.EPORNER_HLS)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(options.get("_force_generic_extractor") for _url, options in attempts))
        self.assertTrue(all("impersonate" not in options for _url, options in attempts))
