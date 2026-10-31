from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hybrid_core.batch import batch_summary, normalize_batch_urls
from hybrid_core.jobs import JobManager


def wait_for_batch(manager: JobManager, batch_id: str, *, timeout: float = 4.0) -> dict:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        summary = manager.get_batch(batch_id)
        if summary["active"] == 0:
            return summary
        time.sleep(0.02)
    return manager.get_batch(batch_id)


class BatchDownloadTests(unittest.TestCase):
    def test_validates_deduplicates_and_limits_urls(self):
        urls, duplicates = normalize_batch_urls(
            [" https://example.com/1 ", "https://example.com/2", "https://example.com/1"]
        )
        self.assertEqual(urls, ["https://example.com/1", "https://example.com/2"])
        self.assertEqual(duplicates, 1)
        with self.assertRaises(ValueError):
            normalize_batch_urls(["file:///tmp/video"])
        with self.assertRaises(ValueError):
            normalize_batch_urls(["https://example.com/video"] * 51)

    def test_batch_summary_counts_mixed_states(self):
        items = [
            {"request": {"batch_index": 1, "mode": "Video"}, "status": "completed", "progress": 1, "created_at": 1},
            {"request": {"batch_index": 2, "mode": "Video"}, "status": "failed", "progress": 0.2, "created_at": 2},
            {"request": {"batch_index": 3, "mode": "Video"}, "status": "queued", "progress": 0, "created_at": 3},
        ]
        result = batch_summary("batch1", items)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["active"], 1)
        self.assertEqual(result["status"], "queued")

    @patch("hybrid_core.jobs.download_from_analysis")
    @patch("hybrid_core.jobs.analyze_url")
    def test_batch_downloads_and_restores_history(self, mocked_analyze, mocked_download):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "jobs.json"
            mocked_analyze.return_value = (
                {"title": "Same Title", "filename": "Same Title.mp4"},
                {"formats": [{"url": "https://media.example/video.mp4", "filesize": 512}]},
            )
            def make_output(**kwargs):
                output = Path(folder) / (kwargs["filename"] + ".mp4")
                output.write_bytes(b"test")
                return output
            mocked_download.side_effect = make_output

            manager = JobManager(max_downloads=2, state_path=state_path)
            batch = manager.start_batch(
                urls=["https://example.com/one", "https://example.com/two", "https://example.com/one"],
                mode="Video",
                video_quality="720p",
                audio_format="MP3",
                audio_quality="192",
                download_dir=folder,
            )
            self.assertEqual(batch["total"], 2)
            self.assertEqual(batch["duplicates_skipped"], 1)
            done = wait_for_batch(manager, batch["id"])
            self.assertEqual(done["completed"], 2, done)
            self.assertEqual(done["status"], "completed")
            names = {item["request"]["filename"] for item in done["items"]}
            self.assertEqual(len(names), 2)
            self.assertTrue(all(name.startswith(batch["id"][:8]) for name in names))

            restored = JobManager(max_downloads=1, state_path=state_path)
            restored_batch = restored.get_batch(batch["id"])
            self.assertEqual(restored_batch["completed"], 2)
            self.assertEqual(len(restored.list_batches()), 1)

    @patch("hybrid_core.jobs.download_from_analysis")
    @patch("hybrid_core.jobs.analyze_url")
    def test_retry_failed_batch_member_only(self, mocked_analyze, mocked_download):
        with tempfile.TemporaryDirectory() as folder:
            mocked_analyze.return_value = (
                {"title": "Test"},
                {"formats": [{"url": "https://media.example/video.mp4", "filesize": 512}]},
            )
            failures = set()
            def download(**kwargs):
                name = kwargs["filename"]
                if "_001_" in name and name not in failures:
                    failures.add(name)
                    raise RuntimeError("Temporary failure")
                return Path(folder) / (name + ".mp4")
            mocked_download.side_effect = download
            manager = JobManager(max_downloads=1)
            batch = manager.start_batch(
                urls=["https://example.com/one", "https://example.com/two"],
                mode="Video",
                video_quality="720p", audio_format="MP3", audio_quality="192", download_dir=folder,
            )
            first = wait_for_batch(manager, batch["id"])
            self.assertEqual(first["failed"], 1, first)
            self.assertEqual(first["completed"], 1, first)
            manager.retry_batch(batch["id"])
            retried = wait_for_batch(manager, batch["id"])
            self.assertEqual(retried["completed"], 2, retried)
            self.assertEqual(mocked_download.call_count, 3)

    @patch("hybrid_core.jobs.download_from_analysis")
    @patch("hybrid_core.jobs.analyze_url")
    def test_cancel_batch_stops_queued_jobs(self, mocked_analyze, mocked_download):
        started = threading.Event()
        release = threading.Event()
        def analyze(*args, **kwargs):
            started.set()
            release.wait(timeout=3)
            return {"title": "Test"}, {"formats": [{"url": "https://media.example/video.mp4"}]}
        mocked_analyze.side_effect = analyze
        mocked_download.return_value = Path("unused.mp4")
        with tempfile.TemporaryDirectory() as folder:
            manager = JobManager(max_downloads=1)
            batch = manager.start_batch(
                urls=[f"https://example.com/{i}" for i in range(3)],
                mode="Video",
                video_quality="720p", audio_format="MP3", audio_quality="192", download_dir=folder,
            )
            try:
                self.assertTrue(started.wait(timeout=1))
                manager.cancel_batch(batch["id"])
            finally:
                release.set()
            done = wait_for_batch(manager, batch["id"])
            self.assertEqual(done["cancelled"], 3, done)
            mocked_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
