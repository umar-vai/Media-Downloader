from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from capture_quality import capture_rank, quality_summary_from_info, related_capture_candidates, select_best_capture


class CaptureQualityTests(unittest.TestCase):
    def test_quality_summary_exposes_best_and_available_resolutions(self) -> None:
        summary = quality_summary_from_info(
            {
                "formats": [
                    {"height": 480, "width": 854, "fps": 30, "vcodec": "h264"},
                    {"height": 720, "width": 1280, "fps": 30, "vcodec": "h264"},
                    {"height": 1080, "width": 1920, "fps": 60, "vcodec": "h264"},
                    {"vcodec": "none", "acodec": "aac"},
                ]
            }
        )
        self.assertEqual(summary["height"], 1080)
        self.assertEqual(summary["width"], 1920)
        self.assertEqual(summary["quality_label"], "Best 1080p60")
        self.assertEqual(summary["available_qualities"], ["1080p", "720p", "480p"])
        self.assertTrue(summary["has_multiple_qualities"])

    def test_best_capture_prefers_higher_resolution(self) -> None:
        selected = {
            "id": "720",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "height": 720,
            "width": 1280,
            "captured_at": 20,
        }
        high = {
            "id": "1080",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "height": 1080,
            "width": 1920,
            "captured_at": 10,
        }
        best = select_best_capture([selected, high], selected)
        self.assertEqual(best["id"], "1080")

    def test_capture_batch_prevents_stale_higher_quality_selection(self) -> None:
        fresh = {
            "id": "fresh-720",
            "capture_group_id": "batch-new",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "url": "https://cdn.example.com/new-720.m3u8",
            "height": 720,
            "width": 1280,
            "captured_at": 200,
        }
        stale = {
            "id": "stale-1080",
            "capture_group_id": "batch-old",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "url": "https://cdn.example.com/old-1080.m3u8",
            "height": 1080,
            "width": 1920,
            "captured_at": 100,
        }
        candidates = related_capture_candidates([stale, fresh], fresh)
        self.assertEqual([item["id"] for item in candidates], ["fresh-720"])
        self.assertEqual(select_best_capture([stale, fresh], fresh)["id"], "fresh-720")

    def test_related_candidates_are_sorted_best_first(self) -> None:
        low = {
            "id": "low",
            "capture_group_id": "batch-1",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "url": "https://cdn.example.com/720.m3u8",
            "height": 720,
            "width": 1280,
            "captured_at": 100,
        }
        high = {
            "id": "high",
            "capture_group_id": "batch-1",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "url": "https://cdn.example.com/1080.m3u8",
            "height": 1080,
            "width": 1920,
            "captured_at": 99,
        }
        candidates = related_capture_candidates([low, high], low)
        self.assertEqual([item["id"] for item in candidates], ["high", "low"])

    def test_same_resolution_prefers_master_playlist(self) -> None:
        variant = {
            "id": "variant",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "height": 1080,
            "width": 1920,
            "available_qualities": ["1080p"],
            "captured_at": 20,
        }
        master = {
            "id": "master",
            "page_url": "https://example.com/watch/1",
            "title": "Episode",
            "tab_id": 1,
            "kind": "hls",
            "height": 1080,
            "width": 1920,
            "available_qualities": ["1080p", "720p", "480p"],
            "captured_at": 10,
        }
        self.assertGreater(capture_rank(master), capture_rank(variant))
        best = select_best_capture([variant, master], variant)
        self.assertEqual(best["id"], "master")


if __name__ == "__main__":
    unittest.main()
