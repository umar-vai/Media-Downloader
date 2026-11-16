from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import file_drag
import single_instance


class FileDragTests(unittest.TestCase):
    def test_usable_paths_keeps_existing_files_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.mp4"
            b = Path(tmp) / "b.zip"
            a.write_bytes(b"1")
            b.write_bytes(b"2")
            paths = file_drag.usable_paths([str(a), str(a), "", str(Path(tmp) / "missing.mp3"), str(b), tmp])
            self.assertEqual([Path(p).name for p in paths], ["a.mp4", "b.zip"])
            self.assertEqual(Path(file_drag.common_parent(paths) or "").resolve(), Path(tmp).resolve())

    def test_common_parent_of_mixed_folders(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            self.assertIsNone(file_drag.common_parent([str(Path(one) / "x"), str(Path(two) / "y")]))

    def test_nothing_to_drag(self) -> None:
        self.assertFalse(file_drag.drag_files(0, []))
        self.assertEqual(file_drag.copy_files([]), 0)

    @unittest.skipUnless(os.name == "nt", "Windows shell only")
    def test_shell_data_object_for_files(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            same = [Path(one) / "a.mp4", Path(one) / "b.mp3"]
            other = Path(two) / "c.pdf"
            for path in [*same, other]:
                path.write_bytes(b"data")
            for group in ([str(p) for p in same], [str(same[0]), str(other)]):
                data = file_drag._data_object(file_drag.usable_paths(group))
                self.assertTrue(data)
                file_drag._release(data)


class SingleInstanceTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "non-Windows fallback")
    def test_other_systems_do_nothing(self) -> None:
        self.assertFalse(single_instance.signal_existing_instance())
        self.assertFalse(single_instance.listen_for_show_requests(lambda: None))

    @unittest.skipUnless(os.name == "nt", "Windows events only")
    def test_second_launch_shows_the_running_window(self) -> None:
        shown = threading.Event()
        self.assertTrue(single_instance.listen_for_show_requests(shown.set))
        self.assertTrue(single_instance.signal_existing_instance())
        self.assertTrue(shown.wait(5))


if __name__ == "__main__":
    unittest.main()
