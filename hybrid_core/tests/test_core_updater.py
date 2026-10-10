from __future__ import annotations

import unittest

from hybrid_core.core_updater import _expected_checksum, select_release, version_tuple


class CoreUpdaterTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(version_tuple("0.3.1"), (0, 3, 1))
        self.assertEqual(version_tuple("v2.10.4"), (2, 10, 4))

    def test_select_release_ignores_desktop_tags(self):
        releases = [
            {"tag_name": "v3.9.5", "draft": False, "prerelease": False},
            {"tag_name": "core-v0.3.0", "draft": False, "prerelease": False},
            {"tag_name": "core-v0.4.0", "draft": False, "prerelease": True},
        ]
        stable = select_release(releases, channel="stable")
        beta = select_release(releases, channel="beta")
        self.assertEqual(stable["tag_name"], "core-v0.3.0")
        self.assertEqual(beta["tag_name"], "core-v0.4.0")

    def test_checksum_parser_matches_asset(self):
        digest = "a" * 64
        text = f"{digest}  MediaDownloaderCore.exe\n"
        self.assertEqual(_expected_checksum(text, "MediaDownloaderCore.exe"), digest)


if __name__ == "__main__":
    unittest.main()
