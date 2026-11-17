from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from edit_timeline import GAP, KEEP, REMOVED, EditTimeline, snap


class EditTimelineTests(unittest.TestCase):
    def make(self) -> EditTimeline:
        edit = EditTimeline(10.0)
        for cut in (2.0, 4.0, 7.0):
            self.assertTrue(edit.split_at(cut))
        return edit

    def test_split_creates_segments(self) -> None:
        edit = self.make()
        self.assertEqual(edit.cuts, [2.0, 4.0, 7.0])
        self.assertEqual([(s.start, s.end) for s in edit.segments()], [(0, 2), (2, 4), (4, 7), (7, 10)])
        self.assertEqual(edit.index_at(3.0), 1)
        self.assertEqual(edit.index_at(7.0), 3)

    def test_split_rejects_edges_and_duplicates(self) -> None:
        edit = self.make()
        self.assertFalse(edit.split_at(0.0))
        self.assertFalse(edit.split_at(10.0))
        self.assertFalse(edit.split_at(4.01))
        self.assertEqual(len(edit.cuts), 3)

    def test_split_inherits_state(self) -> None:
        edit = self.make()
        edit.delete([2], ripple=False)
        edit.split_at(5.0)
        self.assertEqual(edit.states, [KEEP, KEEP, GAP, GAP, KEEP])

    def test_ripple_delete_drops_part(self) -> None:
        edit = self.make()
        edit.delete([1], ripple=True)
        self.assertEqual(edit.pieces(), [(0.0, 2.0, KEEP), (4.0, 10.0, KEEP)])
        self.assertAlmostEqual(edit.output_duration(), 8.0)

    def test_delete_without_ripple_leaves_gap(self) -> None:
        edit = self.make()
        edit.delete([1], ripple=False)
        self.assertEqual(edit.pieces(), [(0.0, 2.0, KEEP), (2.0, 4.0, GAP), (4.0, 10.0, KEEP)])
        self.assertAlmostEqual(edit.output_duration(), 10.0)

    def test_pieces_clip_to_in_out(self) -> None:
        edit = self.make()
        edit.delete([2], ripple=True)
        self.assertEqual(edit.pieces(1.0, 8.0), [(1.0, 4.0, KEEP), (7.0, 8.0, KEEP)])

    def test_all_deleted_has_nothing(self) -> None:
        edit = self.make()
        edit.delete(range(4), ripple=False)
        self.assertEqual(edit.pieces(), [])
        edit.delete(range(4), ripple=True)
        self.assertEqual(edit.output_duration(), 0.0)

    def test_restore_and_close_gaps(self) -> None:
        edit = self.make()
        edit.delete([1, 2], ripple=False)
        self.assertTrue(edit.close_gaps())
        self.assertEqual(edit.states, [KEEP, REMOVED, REMOVED, KEEP])
        edit.restore([1])
        self.assertEqual(edit.states[1], KEEP)

    def test_join_and_move_cut(self) -> None:
        edit = self.make()
        self.assertEqual(edit.move_cut(1, 1.0), 2.0 + 0.04)  # clamped after the previous cut
        self.assertTrue(edit.join(0))
        self.assertEqual(len(edit.cuts), 2)

    def test_next_playable_skips_removed(self) -> None:
        edit = self.make()
        edit.delete([1, 2], ripple=True)
        self.assertEqual(edit.next_playable(3.0), 7.0)
        self.assertEqual(edit.next_playable(1.0), 1.0)
        self.assertIsNone(edit.next_playable(3.0, end=6.0))

    def test_output_mapping(self) -> None:
        edit = self.make()
        edit.delete([1], ripple=True)
        self.assertAlmostEqual(edit.source_to_output(5.0), 3.0)
        self.assertAlmostEqual(edit.output_to_source(3.0), 5.0)

    def test_pieces_from_limits_output(self) -> None:
        edit = self.make()
        edit.delete([1], ripple=True)
        self.assertEqual(edit.pieces_from(1.0, 3.0), [(1.0, 2.0, KEEP), (4.0, 6.0, KEEP)])

    def test_snapshot_round_trip(self) -> None:
        edit = self.make()
        edit.delete([3], ripple=False)
        snapshot = edit.snapshot()
        other = EditTimeline(10.0)
        other.load(*snapshot)
        self.assertEqual(other.cuts, edit.cuts)
        self.assertEqual(other.states, edit.states)
        other.load((1.0,), ("bogus", KEEP))
        self.assertEqual(other.states, [KEEP, KEEP])

    def test_snap(self) -> None:
        self.assertEqual(snap(5.05, [5.0, 6.0], 0.1), 5.0)
        self.assertEqual(snap(5.5, [5.0, 6.0], 0.1), 5.5)


if __name__ == "__main__":
    unittest.main()
