from __future__ import annotations

import unittest

from hybrid_core.agent import make_tray_icon, self_test


class AgentTests(unittest.TestCase):
    def test_generated_tray_icon(self):
        image = make_tray_icon()
        self.assertEqual(image.size, (64, 64))

    def test_developer_self_test(self):
        self.assertEqual(self_test(None), 0)


if __name__ == "__main__":
    unittest.main()
