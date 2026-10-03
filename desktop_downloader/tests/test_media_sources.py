import unittest

from media_sources import detect_platform, is_supported_media_url, platform_name


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


if __name__ == "__main__":
    unittest.main()
