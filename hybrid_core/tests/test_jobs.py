from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid_core.jobs import JobManager


class JobManagerTests(unittest.TestCase):
    @patch("hybrid_core.jobs.analyze_url")
    def test_analysis_job_completes_and_keeps_cached_info_private(self, mocked):
        mocked.return_value = (
            {"title": "Demo", "qualities": ["720p", "Best available"]},
            {"id": "abc", "formats": [{"url": "https://cdn.example/video.mp4"}]},
        )
        manager = JobManager(max_downloads=1)
        job = manager.start_analysis("https://example.com/watch/abc")

        for _ in range(100):
            state = manager.snapshot(job.id)
            if state and state["status"] in {"completed", "failed"}:
                break
            import time
            time.sleep(0.01)

        state = manager.snapshot(job.id)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["result"]["title"], "Demo")
        self.assertNotIn("cached_info", state)


    @patch("hybrid_core.jobs.download_from_analysis")
    def test_completed_download_history_survives_restart(self, mocked_download):
        with tempfile.TemporaryDirectory() as folder:
            state_file = Path(folder) / "jobs.json"
            output = Path(folder) / "video.mp4"
            output.write_bytes(b"demo")
            mocked_download.return_value = output

            manager = JobManager(max_downloads=1, state_path=state_file)
            analysis = manager._new("analysis", {"url": "https://example.com/video"})
            analysis.status = "completed"
            analysis.private["cached_info"] = {"formats": [{"url": "https://cdn.example/video.mp4"}]}

            download = manager.start_download(
                analysis_id=analysis.id,
                mode="Video",
                video_quality="720p",
                audio_format="MP3",
                audio_quality="192",
                filename="video",
                download_dir=folder,
            )

            for _ in range(100):
                state = manager.snapshot(download.id)
                if state and state["status"] == "completed":
                    break
                time.sleep(0.01)

            restored = JobManager(max_downloads=1, state_path=state_file)
            items = restored.list_kind("download")
            self.assertEqual(items[0]["status"], "completed")
            self.assertEqual(items[0]["result"]["filename"], "video.mp4")

    @patch("hybrid_core.jobs.run_export")
    def test_editor_export_reports_completed_output(self, mocked_export):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "edited.mp4"
            output.write_bytes(b"demo")
            mocked_export.return_value = output

            manager = JobManager(max_downloads=1)
            job = manager.start_editor_export(
                {
                    "source_path": str(Path(folder) / "source.mp4"),
                    "output_dir": folder,
                    "output_name": "edited",
                    "start": 0,
                    "end": 5,
                    "crop_preset": "Original",
                    "rotate": "0°",
                    "speed": 1,
                    "mute": False,
                    "volume_percent": 100,
                    "fade_in": 0,
                    "fade_out": 0,
                    "quality": "Balanced",
                }
            )

            for _ in range(100):
                state = manager.snapshot(job.id)
                if state and state["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)

            state = manager.snapshot(job.id)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["result"]["filename"], "edited.mp4")

    def test_interrupted_download_is_restored_as_retryable_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            state_file = Path(folder) / "jobs.json"
            state_file.write_text(
                '{"version":1,"jobs":[{"id":"abc","kind":"download","status":"running","progress":0.4,"detail":"Downloading","error":"","created_at":1,"updated_at":2,"request":{"url":"https://example.com/a","filename":"a"},"result":{}}]}',
                encoding="utf-8",
            )
            restored = JobManager(max_downloads=1, state_path=state_file)
            state = restored.snapshot("abc")
            self.assertEqual(state["status"], "failed")
            self.assertIn("Retry", state["error"])

if __name__ == "__main__":
    unittest.main()
