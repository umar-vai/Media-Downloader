from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from download_queue import DownloadQueue


class DownloadQueueTests(unittest.TestCase):
    def test_fifo_across_link_and_capture_requests(self) -> None:
        queue = DownloadQueue()
        first = queue.enqueue("link", {"url": "https://example.com/1"})
        second = queue.enqueue(
            "capture",
            {"capture": {"id": "cap-1"}},
            capture_id="cap-1",
        )

        running = queue.start_next()
        self.assertIsNotNone(running)
        self.assertEqual(running.id, first.id)
        self.assertEqual(queue.queued_count(), 1)
        self.assertEqual(queue.running_count(), 1)

        queue.complete(first.id)
        running = queue.start_next()
        self.assertIsNotNone(running)
        self.assertEqual(running.id, second.id)
        self.assertEqual(queue.running_count(), 1)

    def test_capture_state_tracks_queue_position_and_progress(self) -> None:
        queue = DownloadQueue()
        queue.enqueue("link", {"url": "https://example.com/1"})
        capture = queue.enqueue(
            "capture",
            {"capture": {"id": "cap-2"}},
            capture_id="cap-2",
        )

        state = queue.latest_for_capture("cap-2")
        self.assertEqual(state["status"], "queued")
        self.assertEqual(state["position"], 2)

        first = queue.start_next()
        queue.complete(first.id)
        running = queue.start_next()
        self.assertEqual(running.id, capture.id)

        queue.update_progress(capture.id, 0.42, "4 MB/s")
        state = queue.latest_for_capture("cap-2")
        self.assertEqual(state["status"], "running")
        self.assertAlmostEqual(state["progress"], 0.42)
        self.assertEqual(state["detail"], "4 MB/s")

    def test_queued_capture_can_be_cancelled_without_affecting_active_job(self) -> None:
        queue = DownloadQueue()
        active = queue.enqueue("link", {"url": "https://example.com/1"})
        capture = queue.enqueue(
            "capture",
            {"capture": {"id": "cap-3"}},
            capture_id="cap-3",
        )
        queue.start_next()

        self.assertTrue(queue.cancel(capture.id))
        self.assertEqual(queue.latest_for_capture("cap-3")["status"], "cancelled")
        self.assertEqual(queue.active().id, active.id)
        self.assertEqual(queue.queued_count(), 0)


if __name__ == "__main__":
    unittest.main()
