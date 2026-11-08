from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from batch_download import batch_filename, batch_summaries, parse_batch_urls
from download_queue import DownloadQueue


class DesktopBatchTests(unittest.TestCase):
    def test_validates_and_deduplicates_input(self) -> None:
        urls, duplicates = parse_batch_urls(
            " https://example.com/one\nhttps://example.com/two\nhttps://example.com/one  "
        )
        self.assertEqual(urls, ["https://example.com/one", "https://example.com/two"])
        self.assertEqual(duplicates, 1)
        for bad in ("", "file:///tmp/a.mp4", "https://name:secret@example.com/video"):
            with self.assertRaises(ValueError):
                parse_batch_urls(bad)
        with self.assertRaises(ValueError):
            parse_batch_urls("\n".join(f"https://example.com/{i}" for i in range(51)))

    def test_batch_filename_is_plain_sanitized_title(self) -> None:
        name = batch_filename("12345678abcdef", 1, "Same: Video.mp4")
        self.assertEqual(name, batch_filename("other", 7, "Same: Video.mp4"))
        self.assertFalse(name.startswith("12345678"))
        self.assertNotIn(":", name)
        self.assertIn("Same", name)

    def test_pause_resume_and_retry_cancelled(self) -> None:
        queue = DownloadQueue(max_concurrent=1)
        first = queue.enqueue({"url": "https://example.com/a", "name": "a"})
        second = queue.enqueue({"url": "https://example.com/b", "name": "b"})
        queue.start_available()
        self.assertEqual(queue.pause(second.id), "paused")
        self.assertEqual(queue.pause(first.id), "pausing")
        queue.mark_paused(first.id)
        self.assertEqual(queue.running_count(), 0)
        self.assertEqual(queue.get(first.id)["status"], "paused")
        self.assertTrue(queue.resume(first.id))
        self.assertEqual(queue.get(first.id)["status"], "queued")
        self.assertEqual(queue.request_cancel(second.id), "cancelled")
        self.assertTrue(queue.retry(second.id))
        self.assertEqual(queue.get(second.id)["status"], "queued")

    def test_atomic_enqueue_preserves_order_and_respects_concurrency(self) -> None:
        queue = DownloadQueue(max_concurrent=3)
        payloads = [
            {"url": f"https://example.com/{i}", "name": f"Batch {i}", "batch_id": "abc123", "batch_index": i}
            for i in range(1, 6)
        ]
        created = queue.enqueue_batch(payloads)
        self.assertEqual(len(created), 5)
        started = queue.start_available()
        self.assertEqual([item.id for item in started], [item.id for item in created[:3]])
        self.assertEqual(queue.queued_count(), 2)

        queue.update_name(created[0].id, "Title from analysis")
        self.assertEqual(queue.get(created[0].id)["name"], "Title from analysis")
        self.assertEqual(queue.get(created[0].id)["batch_id"], "abc123")

        self.assertEqual(queue.request_cancel(created[4].id), "cancelled")
        queue.fail(created[0].id, "network failure")
        snapshots = queue.snapshot()
        summary = batch_summaries(snapshots)[0]
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["cancelled"], 1)
        self.assertEqual(summary["active"], 3)
        self.assertTrue(queue.retry(created[0].id))

    def test_history_limit_does_not_drop_active_batch_members(self) -> None:
        queue = DownloadQueue(max_concurrent=1, history_limit=20)
        payloads = [
            {"url": f"https://example.com/{i}", "name": f"Batch {i}", "batch_id": "batch", "batch_index": i}
            for i in range(50)
        ]
        queue.enqueue_batch(payloads)
        self.assertEqual(queue.queued_count(), 50)
        self.assertEqual(len(queue.snapshot()), 50)

    def test_reject_queue_over_capacity_without_partial_enqueue(self) -> None:
        queue = DownloadQueue(max_concurrent=3)
        for i in range(120):
            queue.enqueue({"url": f"https://example.com/{i}"})
        before = len(queue.snapshot())
        with self.assertRaises(ValueError):
            queue.enqueue_batch([{"url": f"https://example.com/other/{i}"} for i in range(40)])
        self.assertEqual(len(queue.snapshot()), before)

    @patch("app.shared_download_from_analysis")
    @patch("app.shared_analyze_url")
    def test_batch_worker_analyses_every_url_before_download(self, analyze, download) -> None:
        from app import DownloaderApp

        class FakeApp:
            def __init__(self):
                self.events = []
                self._reserved_names = {}
                self._name_lock = threading.Lock()
            _reserve_name = DownloaderApp._reserve_name
            def _put_download_event(self, kind, request_id, data=None):
                self.events.append((kind, request_id, data))

        fake = FakeApp()
        analyze.return_value = (
            {"title": "A: Title.mp4", "creator": "Creator", "platform": "Test", "duration": 17},
            {"formats": [{"url": "https://cdn.example/stream.mp4"}]},
        )
        download.return_value = Path("out.mp4")
        request_settings = {
            "batch_id": "abcdef123456", "batch_index": 2,
            "mode": "Video", "video_quality": "720p",
            "audio_format": "mp3", "audio_quality": "192",
            "source_url": "https://example.com/watch",
        }
        DownloaderApp._download_worker(
            fake,
            "job1",
            threading.Event(),
            "https://example.com/watch",
            "placeholder",
            Path("Downloads"),
            request_settings,
            cached_info=None,
        )

        analyze.assert_called_once()
        kwargs = download.call_args.kwargs
        self.assertEqual(kwargs["cached_info"]["formats"][0]["url"], "https://cdn.example/stream.mp4")
        self.assertEqual(kwargs["filename"], "A_ Title")
        self.assertEqual(request_settings["history_title"], "A: Title.mp4")
        self.assertIn("download_done", [item[0] for item in fake.events])
        self.assertIn("download_finished", [item[0] for item in fake.events])


if __name__ == "__main__":
    unittest.main()


class BatchParsingRobustnessTests(unittest.TestCase):
    def test_bad_lines_are_skipped_instead_of_rejecting_the_batch(self) -> None:
        from batch_download import parse_batch_urls_detailed

        urls, duplicates, invalid = parse_batch_urls_detailed(
            "1. https://example.com/a,\n'https://example.com/b'\nftp://example.com/c\n"
            "https://example.com/a\nhttps://u:p@example.com/x\n"
        )
        self.assertEqual(urls, ["https://example.com/a", "https://example.com/b"])
        self.assertEqual(duplicates, 1)
        self.assertGreaterEqual(invalid, 2)

    def test_queue_clear_finished_keeps_active_items(self) -> None:
        queue = DownloadQueue(max_concurrent=1)
        first = queue.enqueue({"url": "https://example.com/1"})
        second = queue.enqueue({"url": "https://example.com/2"})
        queue.start_available()
        queue.complete(first.id, path="C:/x/a.mp4")
        self.assertEqual(queue.get(first.id)["result_path"], "C:/x/a.mp4")
        self.assertEqual(queue.clear_finished(), 1)
        self.assertIsNone(queue.get(first.id))
        self.assertIsNotNone(queue.get(second.id))
