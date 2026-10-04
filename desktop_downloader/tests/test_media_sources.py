from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yt_dlp.extractor.instagram import InstagramBaseIE

from media_sources import detect_platform, extraction_attempts, facebook_mobile_watch_url, facebook_share_variants, is_supported_media_url, platform_name, request_options, video_format_selector


class MediaSourceTests(unittest.TestCase):
    def test_youtube_urls(self):
        for url in (
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.youtube.com/shorts/abc",
        ):
            self.assertEqual(detect_platform(url), "youtube")

    def test_facebook_urls(self):
        for url in (
            "https://www.facebook.com/reel/123",
            "https://www.facebook.com/watch/?v=123",
            "https://www.facebook.com/share/v/ABC123/",
            "https://m.facebook.com/share/r/ABC123/",
            "https://mbasic.facebook.com/share/p/ABC123/",
            "https://fb.watch/abc/",
        ):
            self.assertEqual(detect_platform(url), "facebook")

    def test_instagram_urls(self):
        for url in (
            "https://www.instagram.com/reel/ABC/",
            "https://www.instagram.com/p/ABC/",
        ):
            self.assertEqual(detect_platform(url), "instagram")

    def test_rejects_unsupported_or_non_http_urls(self):
        for url in ("", "not-a-url", "ftp://youtube.com/a", "https://example.com/video"):
            self.assertFalse(is_supported_media_url(url))

    def test_platform_names(self):
        self.assertEqual(platform_name("facebook"), "Facebook")
        self.assertEqual(platform_name("https://instagram.com/reel/ABC/"), "Instagram")

    def test_platform_specific_request_options(self):
        facebook = request_options("https://www.facebook.com/reel/123")
        instagram = request_options("https://www.instagram.com/reel/ABC/")
        youtube = request_options("https://youtu.be/abc")
        self.assertEqual(facebook["impersonate"].client, "chrome")
        self.assertNotIn("impersonate", instagram)
        self.assertNotIn("impersonate", youtube)

    def test_instagram_auto_impersonation_is_disabled(self):
        self.assertIs(InstagramBaseIE._can_impersonate, False)

    def test_social_video_format_fallback(self):
        instagram = video_format_selector("https://www.instagram.com/reel/ABC/", "720p")
        facebook = video_format_selector("https://www.facebook.com/reel/123", "720p")
        youtube = video_format_selector("https://youtu.be/abc", "720p")
        self.assertIn("b[ext=mp4]/b/", instagram)
        self.assertIn("b[ext=mp4]/b/", facebook)
        self.assertNotIn("b[ext=mp4]/b/", youtube)

    def test_social_best_available_selector(self):
        selector = video_format_selector("https://www.instagram.com/reel/ABC/", "Best available")
        self.assertTrue(selector.startswith("b[ext=mp4]/b/"))

    def test_facebook_mobile_watch_url(self):
        self.assertEqual(
            facebook_mobile_watch_url("https://www.facebook.com/reel/1067651965750372"),
            "https://m.facebook.com/watch/?v=1067651965750372&_rdr",
        )

    def test_facebook_share_variants(self):
        variants = facebook_share_variants("https://www.facebook.com/share/v/1HsYiLvx31/")
        self.assertEqual(variants[0], "https://m.facebook.com/share/v/1HsYiLvx31/")
        self.assertEqual(variants[1], "https://mbasic.facebook.com/share/v/1HsYiLvx31/")

    def test_facebook_share_connection_fallbacks(self):
        attempts = extraction_attempts("https://www.facebook.com/share/v/1HsYiLvx31/")
        urls = [url for url, _options in attempts]
        self.assertIn("https://m.facebook.com/share/v/1HsYiLvx31/", urls)
        self.assertIn("https://mbasic.facebook.com/share/v/1HsYiLvx31/", urls)
        self.assertIn("https://www.facebook.com/share/v/1HsYiLvx31/", urls)
        self.assertNotIn("impersonate", attempts[0][1])

    def test_facebook_connection_fallbacks(self):
        attempts = extraction_attempts("https://www.facebook.com/reel/1067651965750372")
        self.assertGreaterEqual(len(attempts), 4)
        self.assertTrue(attempts[0][0].startswith("https://m.facebook.com/watch/"))
        self.assertEqual(attempts[0][1].get("source_address"), "0.0.0.0")
        self.assertNotIn("impersonate", attempts[0][1])
        self.assertEqual(attempts[1][1]["impersonate"].client, "chrome")

    def test_instagram_keeps_single_standard_path(self):
        attempts = extraction_attempts("https://www.instagram.com/reel/ABC/")
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("impersonate", attempts[0][1])


if __name__ == "__main__":
    unittest.main()
