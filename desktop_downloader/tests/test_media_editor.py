from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from media_editor import _even, format_time, parse_time


class MediaEditorHelpersTests(unittest.TestCase):
    def test_parse_seconds(self) -> None:
        self.assertAlmostEqual(parse_time("12.5"), 12.5)

    def test_parse_minute_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("02:03.250"), 123.25)

    def test_parse_hour_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("01:02:03.5"), 3723.5)

    def test_format_time(self) -> None:
        self.assertEqual(format_time(123.25), "02:03.250")
        self.assertEqual(format_time(3723.5), "01:02:03.500")

    def test_even_dimensions(self) -> None:
        self.assertEqual(_even(1081), 1080)
        self.assertEqual(_even(1080), 1080)


if __name__ == "__main__":
    unittest.main()
