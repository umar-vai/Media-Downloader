from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hybrid_core.update_helper import atomic_replace


class UpdateHelperTests(unittest.TestCase):
    def test_atomic_replace_keeps_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "staged.exe"
            target = root / "MediaDownloaderCore.exe"
            source.write_bytes(b"new-core")
            target.write_bytes(b"old-core")

            atomic_replace(source, target)

            self.assertEqual(target.read_bytes(), b"new-core")
            self.assertEqual(target.with_suffix(".exe.previous").read_bytes(), b"old-core")


if __name__ == "__main__":
    unittest.main()
