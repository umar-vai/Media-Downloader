from __future__ import annotations

import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from browser_capture import (
    BrowserCaptureBridge,
    CaptureStore,
    classify_media_url,
    sanitize_capture,
    sanitize_headers,
)


class BrowserCaptureTests(unittest.TestCase):
    def test_classifies_common_media_types(self) -> None:
        self.assertEqual(classify_media_url("https://cdn.example/video/master.m3u8"), "hls")
        self.assertEqual(classify_media_url("https://cdn.example/manifest", "application/dash+xml"), "dash")
        self.assertEqual(classify_media_url("https://cdn.example/video.mp4"), "direct")
        self.assertEqual(classify_media_url("https://cdn.example/api"), "unknown")

    def test_sanitize_headers_keeps_only_allowed_values(self) -> None:
        headers = sanitize_headers(
            {
                "Referer": "https://example.com/watch",
                "Cookie": "session=abc",
                "X-Secret": "nope",
                "User-Agent": "UA",
            }
        )
        self.assertEqual(headers["Referer"], "https://example.com/watch")
        self.assertEqual(headers["Cookie"], "session=abc")
        self.assertEqual(headers["User-Agent"], "UA")
        self.assertNotIn("X-Secret", headers)

    def test_invalid_capture_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_capture({"url": "file:///C:/video.mp4"})

    def test_capture_group_id_is_preserved(self) -> None:
        item = sanitize_capture(
            {
                "url": "https://cdn.example.com/master.m3u8",
                "page_url": "https://example.com/watch/1",
                "title": "Episode",
                "tab_id": 1,
                "kind": "hls",
                "capture_group_id": "batch-123",
            }
        )
        self.assertEqual(item["capture_group_id"], "batch-123")

    def test_accepts_page_fallback_capture(self) -> None:
        item = sanitize_capture(
            {
                "url": "https://example.com/watch/episode-1",
                "page_url": "https://example.com/watch/episode-1",
                "title": "Episode 1",
                "tab_id": 11,
                "kind": "page",
            }
        )
        self.assertEqual(item["kind"], "page")

    def test_store_preserves_quality_metadata_on_duplicate(self) -> None:
        store = CaptureStore()
        first = store.add(
            {
                "id": "first",
                "url": "https://cdn.example.com/master.m3u8",
                "page_url": "https://example.com/watch/1",
                "title": "Episode",
                "tab_id": 1,
                "kind": "hls",
            }
        )
        store.update_metadata(
            first["id"],
            {
                "quality_status": "ready",
                "quality_label": "Best 1080p",
                "height": 1080,
                "available_qualities": ["1080p", "720p"],
            },
        )
        second = store.add(
            {
                "id": "second",
                "url": "https://cdn.example.com/master.m3u8",
                "page_url": "https://example.com/watch/1",
                "title": "Episode",
                "tab_id": 1,
                "kind": "hls",
            }
        )
        self.assertEqual(second["quality_label"], "Best 1080p")
        self.assertEqual(second["height"], 1080)

    def test_store_collapses_duplicate_tab_and_url(self) -> None:
        store = CaptureStore(limit=10)
        first = store.add(
            {
                "url": "https://cdn.example/video.mp4",
                "tab_id": 5,
                "title": "Old",
            }
        )
        second = store.add(
            {
                "url": "https://cdn.example/video.mp4",
                "tab_id": 5,
                "title": "New",
            }
        )
        self.assertNotEqual(first["id"], second["id"])
        items = store.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "New")

    def test_local_bridge_requires_token_and_accepts_capture(self) -> None:
        store = CaptureStore()
        bridge = BrowserCaptureBridge(store, "correct-token", port=0)
        port = bridge.start()
        self.addCleanup(bridge.stop)

        payload = json.dumps(
            {
                "url": "https://cdn.example/master.m3u8",
                "page_url": "https://example.com/watch/1",
                "title": "Episode 1",
                "tab_id": 7,
                "headers": {"Referer": "https://example.com/watch/1"},
            }
        ).encode("utf-8")

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Media-Downloader-Token": "wrong-token",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        good = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Media-Downloader-Token": "correct-token",
            },
        )
        with urllib.request.urlopen(good, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(store.list()[0]["kind"], "hls")


if __name__ == "__main__":
    unittest.main()
