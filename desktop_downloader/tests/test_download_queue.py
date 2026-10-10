from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from download_queue import DownloadQueue


class DownloadQueueTests(unittest.TestCase):
    def test_fifo_link_downloads(self) -> None:
        queue = DownloadQueue()
        first = queue.enqueue({"url": "https://example.com/1"})
        second = queue.enqueue({"url": "https://example.com/2"})

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

    def test_queue_position_and_progress(self) -> None:
        queue = DownloadQueue()
        queue.enqueue({"url": "https://example.com/1"})
        second = queue.enqueue({"url": "https://example.com/2"})

        self.assertEqual(queue.position(second.id), 2)
        first = queue.start_next()
        queue.update_progress(first.id, 0.42, "4 MB/s")
        self.assertAlmostEqual(queue.active().progress, 0.42)
        self.assertEqual(queue.active().detail, "4 MB/s")

    def test_queued_link_can_be_cancelled_without_affecting_active_job(self) -> None:
        queue = DownloadQueue()
        active = queue.enqueue({"url": "https://example.com/1"})
        queued = queue.enqueue({"url": "https://example.com/2"})
        queue.start_next()

        self.assertTrue(queue.cancel(queued.id))
        self.assertEqual(queue.active().id, active.id)
        self.assertEqual(queue.queued_count(), 0)


if __name__ == "__main__":
    unittest.main()
