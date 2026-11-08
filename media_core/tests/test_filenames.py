from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from media_core.engine import _available_name, _files_with_stem


class FilenameTests(unittest.TestCase):
    def test_titles_with_glob_characters_are_found_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "Song [Live] (Official).mp4").write_bytes(b"x")
            self.assertEqual(
                [p.name for p in _files_with_stem(directory, "Song [Live] (Official)")],
                ["Song [Live] (Official).mp4"],
            )
            self.assertEqual(_available_name(directory, "Song [Live] (Official)"), "Song [Live] (Official) (2)")


if __name__ == "__main__":
    unittest.main()
