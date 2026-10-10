from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yt_dlp.extractor.instagram import InstagramBaseIE

from media_sources import (
    detect_platform,
    extraction_attempts,
    facebook_mobile_watch_url,
    facebook_share_variants,
    is_supported_media_url,
    platform_name,
    request_options,
    video_format_selector,
    eporner_embed_url,
)


class MediaSourceTests(unittest.TestCase):
    def test_known_platform_urls(self):
        self.assertEqual(detect_platform("https://youtu.be/abc"), "youtube")
        self.assertEqual(detect_platform("https://www.facebook.com/reel/123"), "facebook")
        self.assertEqual(detect_platform("https://www.instagram.com/reel/ABC/"), "instagram")

    def test_arbitrary_http_websites_are_accepted(self):
        for url in (
            "https://example.com/video/123",
            "https://media.example.org/watch?id=abc",
            "http://localhost.example/video.mp4",
        ):
            self.assertTrue(is_supported_media_url(url))
            self.assertEqual(detect_platform(url), "web")

    def test_rejects_non_http_urls(self):
        for url in ("", "not-a-url", "ftp://example.com/a", "file:///tmp/video.mp4"):
            self.assertFalse(is_supported_media_url(url))
            self.assertIsNone(detect_platform(url))

    def test_platform_names(self):
        self.assertEqual(platform_name("facebook"), "Facebook")
        self.assertEqual(platform_name("https://instagram.com/reel/ABC/"), "Instagram")
        self.assertEqual(platform_name("https://www.example.com/video"), "example.com")
        self.assertEqual(platform_name("web"), "Website")

    def test_request_options_keep_facebook_impersonation(self):
        facebook = request_options("https://www.facebook.com/reel/123")
        generic = request_options("https://example.com/video")
        self.assertEqual(facebook["impersonate"].client, "chrome")
        self.assertNotIn("impersonate", generic)

    def test_instagram_auto_impersonation_is_disabled(self):
        self.assertIs(InstagramBaseIE._can_impersonate, False)

    def test_generic_site_has_multiple_extraction_fallbacks(self):
        attempts = extraction_attempts("https://example.com/watch/123")
        self.assertGreaterEqual(len(attempts), 4)
        self.assertTrue(any("impersonate" in options for _url, options in attempts))
        self.assertTrue(any(options.get("_force_generic_extractor") for _url, options in attempts))

    def test_instagram_avoids_impersonation_but_has_generic_fallback(self):
        attempts = extraction_attempts("https://www.instagram.com/reel/ABC/")
        self.assertGreaterEqual(len(attempts), 2)
        self.assertTrue(any(options.get("_force_generic_extractor") for _url, options in attempts))
        self.assertTrue(all("impersonate" not in options for _url, options in attempts))

    def test_facebook_mobile_watch_url(self):
        self.assertEqual(
            facebook_mobile_watch_url("https://www.facebook.com/reel/1067651965750372"),
            "https://m.facebook.com/watch/?v=1067651965750372&_rdr",
        )

    def test_facebook_share_variants(self):
        variants = facebook_share_variants("https://www.facebook.com/share/v/1HsYiLvx31/")
        self.assertEqual(variants[0], "https://m.facebook.com/share/v/1HsYiLvx31/")
        self.assertEqual(variants[1], "https://mbasic.facebook.com/share/v/1HsYiLvx31/")

    def test_facebook_connection_fallbacks(self):
        attempts = extraction_attempts("https://www.facebook.com/reel/1067651965750372")
        self.assertGreaterEqual(len(attempts), 3)
        self.assertTrue(attempts[0][0].startswith("https://m.facebook.com/watch/"))
        self.assertEqual(attempts[0][1].get("source_address"), "0.0.0.0")
        self.assertTrue(any("impersonate" in options for _url, options in attempts))

    def test_eporner_https_api_patch_is_installed(self):
        self.assertTrue(getattr(InstagramBaseIE, "_can_impersonate", None) is False)
        from yt_dlp.extractor.eporner import EpornerIE
        self.assertTrue(getattr(EpornerIE, "_media_downloader_https_patch", False))

    def test_eporner_uses_embed_and_true_generic_fallbacks_without_impersonation(self):
        url = "https://www.eporner.com/video-AbC123xyz/sample-title/"
        self.assertEqual(
            eporner_embed_url(url),
            "https://www.eporner.com/embed/AbC123xyz/",
        )
        attempts = extraction_attempts(url)
        urls = [candidate for candidate, _options in attempts]
        self.assertIn(url, urls)
        self.assertIn("https://www.eporner.com/embed/AbC123xyz/", urls)
        self.assertTrue(any(options.get("_force_generic_extractor") for _url, options in attempts))
        self.assertTrue(all("impersonate" not in options for _url, options in attempts))
        self.assertTrue(
            all("prefer-legacy-http-handler" in options.get("compat_opts", set()) for _url, options in attempts)
        )

    def test_generic_fallback_uses_execution_marker(self):
        attempts = extraction_attempts("https://example.com/watch/123")
        self.assertTrue(any(options.get("_force_generic_extractor") for _url, options in attempts))
        self.assertTrue(all("force_generic_extractor" not in options for _url, options in attempts))

    def test_generic_video_format_is_resilient(self):
        selector = video_format_selector("https://example.com/video", "720p")
        self.assertIn("b[ext=mp4]/b/", selector)
        self.assertIn("bv*+ba", selector)

    def test_youtube_format_remains_quality_capped(self):
        selector = video_format_selector("https://youtu.be/abc", "720p")
        self.assertNotIn("b[ext=mp4]/b/", selector)


if __name__ == "__main__":
    unittest.main()
