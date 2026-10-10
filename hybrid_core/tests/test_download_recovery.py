from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hybrid_core.jobs import JobManager
from media_core.engine import Cancelled


class DownloadRecoveryIntegrationTests(unittest.TestCase):
    @staticmethod
    def wait_for(manager: JobManager, job_id: str, statuses: set[str], timeout: float = 2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = manager.snapshot(job_id)
            if state and state["status"] in statuses:
                return state
            time.sleep(0.01)
        return manager.snapshot(job_id)

    @patch("hybrid_core.jobs.download_from_analysis")
    @patch("hybrid_core.jobs.analyze_url")
    def test_running_download_recovers_and_resumes_after_restart(self, mocked_analyze, mocked_download):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state_file = root / "jobs.json"
            state_file.write_text(
                '{"version":2,"jobs":[{"id":"recover1","kind":"download","status":"running","progress":0.42,'
                '"detail":"Downloading","error":"","created_at":1,"updated_at":2,'
                '"request":{"analysis_id":"old","url":"https://example.com/video","mode":"Video",'
                '"video_quality":"720p","audio_format":"MP3","audio_quality":"192","filename":"video",'
                f'"download_dir":"{str(root).replace(chr(92), chr(92)+chr(92))}"},'
                '"result":{},"metrics":{"speed_bps":1000},"meta":{"attempt_count":1}}]}',
                encoding="utf-8",
            )
            mocked_analyze.return_value = (
                {"title": "Video"},
                {"formats": [{"url": "https://cdn.example/fresh.mp4", "filesize": 1024}]},
            )
            output = root / "video.mp4"
            output.write_bytes(b"done")
            mocked_download.return_value = output

            manager = JobManager(max_downloads=1, state_path=state_file)
            state = self.wait_for(manager, "recover1", {"completed", "failed"})

            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["meta"]["recovery_count"], 1)
            self.assertGreaterEqual(state["meta"]["attempt_count"], 2)
            mocked_analyze.assert_called()
            mocked_download.assert_called()

    @patch("hybrid_core.jobs.download_from_analysis")
    @patch("hybrid_core.jobs.analyze_url")
    def test_retry_forces_fresh_analysis_and_tracks_retry_metadata(self, mocked_analyze, mocked_download):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "retry.mp4"
            output.write_bytes(b"done")
            mocked_analyze.return_value = (
                {"title": "Retry"},
                {"formats": [{"url": "https://cdn.example/new-signed-url.mp4", "filesize": 1024}]},
            )
            mocked_download.return_value = output

            manager = JobManager(max_downloads=1)
            job = manager._new(
                "download",
                {
                    "analysis_id": "old",
                    "url": "https://example.com/retry",
                    "mode": "Video",
                    "video_quality": "720p",
                    "audio_format": "MP3",
                    "audio_quality": "192",
                    "filename": "retry",
                    "download_dir": folder,
                },
            )
            job.status = "failed"
            job.private["cached_info"] = {"formats": [{"url": "https://cdn.example/expired.mp4"}]}

            manager.retry_download(job.id)
            state = self.wait_for(manager, job.id, {"completed", "failed"})

            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["meta"]["retry_count"], 1)
            mocked_analyze.assert_called_once()

    @patch("hybrid_core.jobs.download_from_analysis")
    def test_running_download_can_be_cancelled(self, mocked_download):
        def blocked_download(**kwargs):
            cancel_event: threading.Event = kwargs["cancel_event"]
            while not cancel_event.wait(0.01):
                pass
            raise Cancelled("cancelled")

        mocked_download.side_effect = blocked_download
        with tempfile.TemporaryDirectory() as folder:
            manager = JobManager(max_downloads=1)
            analysis = manager._new("analysis", {"url": "https://example.com/cancel"})
            analysis.status = "completed"
            analysis.private["cached_info"] = {"formats": [{"url": "https://cdn.example/video.mp4"}]}

            job = manager.start_download(
                analysis_id=analysis.id,
                mode="Video",
                video_quality="720p",
                audio_format="MP3",
                audio_quality="192",
                filename="cancel",
                download_dir=folder,
            )
            deadline = time.time() + 1
            while time.time() < deadline:
                state = manager.snapshot(job.id)
                if state and state["status"] == "running":
                    break
                time.sleep(0.01)

            self.assertTrue(manager.cancel(job.id))
            state = self.wait_for(manager, job.id, {"cancelled", "failed"})
            self.assertEqual(state["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
