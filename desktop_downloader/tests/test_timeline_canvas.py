"""The magnetic timeline canvas: clicks land on the right part after a ripple delete."""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from edit_timeline import EditTimeline
from media_editor_widgets import TOOL_BLADE, TimelineCanvas


class Event:
    def __init__(self, x: float, y: float = 60) -> None:
        self.x, self.y = x, y


class TimelineCanvasTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"Tk is not available: {exc}")
        self.root.geometry("1024x200")
        self.splits: list[float] = []
        self.canvas = TimelineCanvas(self.root, on_split=self.splits.append)
        self.canvas.pack(fill="both", expand=True)
        self.root.update()
        self.edit = EditTimeline(10.0)
        for cut in (2.0, 5.0, 7.0):
            self.edit.split_at(cut)
        self.canvas.set_media(10.0, 0.0, 10.0, 0.0)
        self.canvas.set_edit(self.edit)

    def tearDown(self) -> None:
        self.root.destroy()

    def x_for(self, sequence_seconds: float) -> float:
        return self.canvas._seq_to_x(sequence_seconds)

    def test_view_shrinks_after_ripple_delete(self) -> None:
        self.edit.delete([1], ripple=True)
        self.canvas.redraw()
        self.assertAlmostEqual(self.canvas.view_end, 7.0)
        # Timeline second 2.5 is now source 5.5 (part 3 moved up against part 1).
        self.assertAlmostEqual(self.canvas._x_to_time(self.x_for(2.5)), 5.5, places=1)

    def test_click_selects_the_part_that_moved_up(self) -> None:
        self.edit.delete([1], ripple=True)
        self.canvas.redraw()
        self.canvas._on_press(Event(self.x_for(3.0)))
        self.assertEqual(self.canvas.selected, {2})

    def test_blade_cuts_in_source_time(self) -> None:
        self.edit.delete([1], ripple=True)
        self.canvas.set_tool(TOOL_BLADE)
        self.canvas.snap_enabled = False
        self.canvas._on_press(Event(self.x_for(5.0)))
        self.assertEqual(len(self.splits), 1)
        self.assertAlmostEqual(self.splits[0], 8.0, places=1)

    def test_playhead_in_a_removed_part_sits_on_the_join(self) -> None:
        self.edit.delete([1], ripple=True)
        self.canvas.set_playhead(3.0)
        self.assertAlmostEqual(self.canvas.to_seq(self.canvas.playhead), 2.0)


if __name__ == "__main__":
    unittest.main()
