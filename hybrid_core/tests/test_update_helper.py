from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hybrid_core.update_helper import (
    atomic_replace,
    restore_previous_after_failed_update,
    swap_with_previous,
)


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


    def test_failed_update_restores_previous_and_keeps_failed_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "staged.exe"
            target = root / "MediaDownloaderCore.exe"
            source.write_bytes(b"new-core")
            target.write_bytes(b"old-core")

            atomic_replace(source, target)
            restore_previous_after_failed_update(target)

            self.assertEqual(target.read_bytes(), b"old-core")
            self.assertEqual(target.with_suffix(".exe.failed").read_bytes(), b"new-core")
            self.assertFalse(target.with_suffix(".exe.previous").exists())

    def test_manual_rollback_swaps_current_and_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "MediaDownloaderCore.exe"
            previous = target.with_suffix(".exe.previous")
            target.write_bytes(b"new-core")
            previous.write_bytes(b"old-core")

            swap_with_previous(target)

            self.assertEqual(target.read_bytes(), b"old-core")
            self.assertEqual(previous.read_bytes(), b"new-core")

if __name__ == "__main__":
    unittest.main()
