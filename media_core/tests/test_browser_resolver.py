from __future__ import annotations

import unittest

from media_core.browser_resolver import (
    _add_cookie_header,
    _cookie_header,
    _format_from_url,
    _looks_like_media,
    _rank_embed_urls,
    hls_master_children,
    order_streams,
)
from media_core.sources import has_separate_audio, video_format_selector


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

    def test_hls_segments_are_not_whole_videos(self):
        self.assertFalse(_looks_like_media("https://cdn.example/seg-12.ts", "video/mp2t"))
        self.assertFalse(_looks_like_media("https://cdn.example/chunk.jpg", "video/mp2t"))

    def test_master_playlist_children(self):
        master = "\n".join([
            "#EXTM3U",
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",URI="audio/eng.m3u8"',
            "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO=\"aud\"",
            "1080/index.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,AUDIO=\"aud\"",
            "https://cdn2.example/720/index.m3u8",
        ])
        self.assertEqual(
            hls_master_children(master, "https://cdn.example/hls/master.m3u8"),
            [
                "https://cdn.example/hls/audio/eng.m3u8",
                "https://cdn.example/hls/1080/index.m3u8",
                "https://cdn2.example/720/index.m3u8",
            ],
        )
        self.assertIsNone(hls_master_children("#EXTM3U\n#EXTINF:4,\nseg1.ts", "https://cdn.example/a.m3u8"))

    def test_master_wins_over_the_variant_the_player_started_with(self):
        streams = [
            {"url": "https://cdn.example/720/index.m3u8", "kind": "hls", "children": None},
            {"url": "https://cdn.example/master.m3u8", "kind": "hls",
             "children": ["https://cdn.example/720/index.m3u8", "https://cdn.example/audio.m3u8"]},
            {"url": "https://cdn.example/audio.m3u8", "kind": "hls", "children": None},
            {"url": "https://cdn.example/clip.mp4", "kind": "file", "children": None},
        ]
        self.assertEqual(
            [item["url"] for item in order_streams(streams)],
            ["https://cdn.example/master.m3u8", "https://cdn.example/clip.mp4"],
        )

    def test_split_audio_streams_are_always_merged(self):
        info = {"formats": [
            {"format_id": "hls-1080", "height": 1080, "url": "v"},
            {"format_id": "hls-audio-eng", "vcodec": "none", "url": "a"},
        ]}
        self.assertTrue(has_separate_audio(info))
        selector = video_format_selector("https://anime.example/ep", "1080p", info)
        self.assertTrue(selector.startswith("bv*[height<=?1080]+ba"))
        self.assertFalse(has_separate_audio({"formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a"}]}))
        self.assertTrue(video_format_selector("https://anime.example/ep", "1080p").startswith("b[height<=1080]"))


if __name__ == "__main__":
    unittest.main()
