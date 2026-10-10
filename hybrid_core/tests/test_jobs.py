from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
