from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

import crop_geometry as cg

W, H = 1920, 1080


def inside(rect: cg.Rect) -> bool:
    x, y, w, h = rect
    return x >= -1e-6 and y >= -1e-6 and x + w <= W + 1e-6 and y + h <= H + 1e-6


class CropGeometryTests(unittest.TestCase):
    def test_centered_ratio_fills_height(self) -> None:
        x, y, w, h = cg.centered(9 / 16, W, H)
        self.assertAlmostEqual(h, H)
        self.assertAlmostEqual(w / h, 9 / 16)
        self.assertAlmostEqual(x, (W - w) / 2)

    def test_move_stays_in_frame(self) -> None:
        rect = cg.move((100, 100, 400, 300), 5000, -5000, W, H)
        self.assertEqual(rect, (W - 400, 0, 400, 300))

    def test_free_corner_resize_keeps_opposite_corner(self) -> None:
        x, y, w, h = cg.resize((100, 100, 400, 300), "se", 50, -20, W, H)
        self.assertEqual((x, y), (100, 100))
        self.assertEqual((w, h), (450, 280))

    def test_free_resize_respects_minimum_and_edges(self) -> None:
        rect = cg.resize((100, 100, 400, 300), "w", 1000, 0, W, H)
        self.assertAlmostEqual(rect[2], cg.MIN_SIZE)
        self.assertAlmostEqual(rect[0] + rect[2], 500)
        rect = cg.resize((100, 100, 400, 300), "nw", -500, -500, W, H)
        self.assertEqual(rect[:2], (0, 0))
        self.assertTrue(inside(rect))

    def test_ratio_corner_resize_keeps_ratio_and_bounds(self) -> None:
        start = cg.centered(1.0, W, H, scale=0.5)
        for dx, dy in ((300, 10), (-200, 400), (5000, 5000), (-5000, -5000)):
            rect = cg.resize(start, "se", dx, dy, W, H, ratio=1.0)
            self.assertAlmostEqual(rect[2], rect[3], places=6)
            self.assertTrue(inside(rect), rect)
            self.assertAlmostEqual(rect[0], start[0])
            self.assertAlmostEqual(rect[1], start[1])

    def test_ratio_edge_resize_keeps_ratio_and_bounds(self) -> None:
        start = (800, 400, 160, 90)
        for handle, dx, dy in (("e", 4000, 0), ("w", -4000, 0), ("n", 0, -4000), ("s", 0, 4000)):
            rect = cg.resize(start, handle, dx, dy, W, H, ratio=16 / 9)
            self.assertAlmostEqual(rect[2] / rect[3], 16 / 9, places=6)
            self.assertTrue(inside(rect), (handle, rect))

    def test_draw_new_box_any_direction(self) -> None:
        rect = cg.from_points(500, 500, 300, 200, W, H)
        self.assertEqual(rect, (300, 200, 200, 300))
        rect = cg.from_points(500, 500, 900, 520, W, H, ratio=9 / 16)
        self.assertAlmostEqual(rect[2] / rect[3], 9 / 16)
        self.assertTrue(inside(rect))

    def test_finalize_gives_even_box_inside_frame(self) -> None:
        box = cg.finalize((1700.4, 901.7, 333.3, 199.9), W, H)
        x, y, w, h = box
        self.assertEqual(w % 2, 0)
        self.assertEqual(h % 2, 0)
        self.assertLessEqual(x + w, W)
        self.assertLessEqual(y + h, H)
        self.assertEqual(cg.finalize(cg.full_frame(1281, 721), 1281, 721), (0, 0, 1280, 720))

    def test_hit_test(self) -> None:
        rect = (100, 100, 400, 300)
        self.assertEqual(cg.hit_test(rect, 100, 100, 8), "nw")
        self.assertEqual(cg.hit_test(rect, 500, 250, 8), "e")
        self.assertEqual(cg.hit_test(rect, 300, 401, 8), "s")
        self.assertEqual(cg.hit_test(rect, 300, 250, 8), "move")
        self.assertIsNone(cg.hit_test(rect, 50, 50, 8))


if __name__ == "__main__":
    unittest.main()
