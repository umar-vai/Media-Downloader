from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from title_fix import episode_from_url, reconcile_title  # noqa: E402


class TitleFixTests(unittest.TestCase):
    def test_stale_episode_number_is_replaced(self) -> None:
        self.assertEqual(
            reconcile_title("Aoashi 2nd Season Episode 8 Online with SUB/DUB",
                            "https://animewave.to/watch/aoashi-2nd-season-x/ep-5"),
            "Aoashi 2nd Season Episode 5")

    def test_generic_title_gets_the_episode(self) -> None:
        self.assertEqual(reconcile_title("Overgeared Anime English SUB/DUB", "https://a.to/watch/o/ep-5"),
                         "Overgeared Anime Episode 5")

    def test_query_style_and_no_episode(self) -> None:
        self.assertEqual(episode_from_url("https://x.com/w?v=1&ep=12"), "12")
        self.assertEqual(reconcile_title("Something", "https://x.com/watch?v=1"), "Something")
        self.assertEqual(reconcile_title("", "https://x.com/ep-3"), "")


if __name__ == "__main__":
    unittest.main()
