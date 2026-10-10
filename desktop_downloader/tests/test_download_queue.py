from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from download_queue import DownloadQueue


class DownloadQueueTests(unittest.TestCase):
    def test_starts_multiple_downloads_concurrently(self) -> None:
        queue = DownloadQueue(max_concurrent=3)
        requests = [
            queue.enqueue({"url": f"https://example.com/{index}", "name": f"Video {index}"})
            for index in range(5)
        ]

        started = queue.start_available()
        self.assertEqual([item.id for item in started], [item.id for item in requests[:3]])
        self.assertEqual(queue.running_count(), 3)
        self.assertEqual(queue.queued_count(), 2)

        queue.complete(requests[0].id)
        newly_started = queue.start_available()
        self.assertEqual([item.id for item in newly_started], [requests[3].id])
        self.assertEqual(queue.running_count(), 3)
        self.assertEqual(queue.queued_count(), 1)

    def test_running_download_can_enter_cancelling_state(self) -> None:
        queue = DownloadQueue(max_concurrent=2)
        request = queue.enqueue({"url": "https://example.com/1", "name": "Video"})
        queue.start_available()

        self.assertEqual(queue.request_cancel(request.id), "cancelling")
        self.assertEqual(queue.get(request.id)["status"], "cancelling")
        self.assertEqual(queue.running_count(), 1)

        self.assertTrue(queue.cancel(request.id))
        self.assertEqual(queue.get(request.id)["status"], "cancelled")
        self.assertEqual(queue.running_count(), 0)

    def test_queued_download_can_be_cancelled_immediately(self) -> None:
        queue = DownloadQueue(max_concurrent=1)
        first = queue.enqueue({"url": "https://example.com/1", "name": "One"})
        second = queue.enqueue({"url": "https://example.com/2", "name": "Two"})
        queue.start_available()

        self.assertEqual(queue.request_cancel(second.id), "cancelled")
        self.assertEqual(queue.get(second.id)["status"], "cancelled")
        self.assertEqual(queue.active().id, first.id)
        self.assertEqual(queue.queued_count(), 0)

    def test_progress_and_snapshot_include_display_name(self) -> None:
        queue = DownloadQueue(max_concurrent=1)
        request = queue.enqueue({"url": "https://example.com/1", "name": "My Video"})
        queue.start_available()
        queue.update_progress(request.id, 0.42, "4 MB/s")

        state = queue.get(request.id)
        self.assertEqual(state["name"], "My Video")
        self.assertAlmostEqual(state["progress"], 0.42)
        self.assertEqual(state["detail"], "4 MB/s")


if __name__ == "__main__":
    unittest.main()
