import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download_list import queue_row  # noqa: E402


class SizeEstimateTests(unittest.TestCase):
    def test_estimated_size_has_tilde_while_downloading(self):
        row = queue_row({"id": "1", "status": "running", "progress": 0.1},
                        {"total_bytes": 3 * 1024 ** 3, "downloaded_bytes": 1, "total_is_estimate": True})
        self.assertEqual(row["size_text"], "~3.00 GB")

    def test_exact_size_has_no_tilde(self):
        row = queue_row({"id": "1", "status": "running", "progress": 0.1},
                        {"total_bytes": 1024 ** 3, "downloaded_bytes": 1, "total_is_estimate": False})
        self.assertEqual(row["size_text"], "1.00 GB")


if __name__ == "__main__":
    unittest.main()
