from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from editor_history import EditorHistory, EditorSnapshot


def snapshot(start: str, speed: str = "1.0x") -> EditorSnapshot:
    return EditorSnapshot(
        start=start,
        end="01:00.000",
        crop="Original",
        rotate="0°",
        speed=speed,
        custom_x="0",
        custom_y="0",
        custom_w="1280",
        custom_h="720",
        mute=False,
        volume=100.0,
        fade_in="0",
        fade_out="0",
    )


class EditorHistoryTests(unittest.TestCase):
    def test_undo_and_redo(self) -> None:
        history = EditorHistory()
        history.push(snapshot("00:00.000"))
        history.push(snapshot("00:10.000"))
        history.push(snapshot("00:20.000"))

        self.assertTrue(history.can_undo)
        self.assertEqual(history.undo(), snapshot("00:10.000"))
        self.assertEqual(history.undo(), snapshot("00:00.000"))
        self.assertFalse(history.can_undo)
        self.assertTrue(history.can_redo)
        self.assertEqual(history.redo(), snapshot("00:10.000"))

    def test_new_edit_discards_redo_branch(self) -> None:
        history = EditorHistory()
        history.push(snapshot("00:00.000"))
        history.push(snapshot("00:10.000"))
        history.undo()
        history.push(snapshot("00:05.000", speed="2.0x"))

        self.assertFalse(history.can_redo)
        self.assertEqual(history.current, snapshot("00:05.000", speed="2.0x"))

    def test_duplicate_snapshot_is_not_added(self) -> None:
        history = EditorHistory()
        item = snapshot("00:00.000")
        self.assertTrue(history.push(item))
        self.assertFalse(history.push(item))
        self.assertFalse(history.can_undo)

    def test_history_is_bounded(self) -> None:
        history = EditorHistory(limit=3)
        history.push(snapshot("00:00.000"))
        history.push(snapshot("00:01.000"))
        history.push(snapshot("00:02.000"))
        history.push(snapshot("00:03.000"))

        self.assertEqual(history.undo(), snapshot("00:02.000"))
        self.assertEqual(history.undo(), snapshot("00:01.000"))
        self.assertFalse(history.can_undo)


if __name__ == "__main__":
    unittest.main()
