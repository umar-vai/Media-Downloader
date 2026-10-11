from __future__ import annotations

import unittest

from media_core.browser_resolver import (
    _add_cookie_header,
    _cookie_header,
    _format_from_url,
    _looks_like_media,
    _rank_embed_urls,
)


class BrowserResolverTests(unittest.TestCase):
    def test_detects_stream_urls(self):
        self.assertTrue(_looks_like_media("https://cdn.example/video/master.m3u8?token=1", ""))
        self.assertTrue(_looks_like_media("https://cdn.example/stream", "application/vnd.apple.mpegurl"))
        self.assertFalse(_looks_like_media("https://cdn.example/poster.jpg", "image/jpeg"))

    def test_builds_hls_format_from_dom_url(self):
        item = _format_from_url(
            "https://example.com/watch/1",
            "https://cdn.example/master.m3u8?token=abc",
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["protocol"], "m3u8_native")
        self.assertEqual(item["ext"], "mp4")
        self.assertEqual(item["http_headers"]["Referer"], "https://example.com/watch/1")

    def test_cookie_header_is_scoped_by_caller_and_attached_in_memory(self):
        header = _cookie_header(
            [
                {"name": "session", "value": "abc"},
                {"name": "consent", "value": "yes"},
            ]
        )
        self.assertEqual(header, "session=abc; consent=yes")
        item = _add_cookie_header({"http_headers": {"Referer": "https://example.com"}}, header)
        self.assertEqual(item["http_headers"]["Cookie"], header)

    def test_embed_urls_prioritize_cross_origin_player_links(self):
        ranked = _rank_embed_urls(
            [
                "https://anime.example/assets/frame",
                "https://player.cdn.example/embed/episode-2",
                "https://ads.example/banner",
            ],
            "https://anime.example/watch/episode-2",
        )
        self.assertEqual(ranked[0], "https://player.cdn.example/embed/episode-2")


if __name__ == "__main__":
    unittest.main()
