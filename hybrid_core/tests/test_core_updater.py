from __future__ import annotations

import unittest

from hybrid_core.core_updater import CoreUpdateService, _expected_checksum, select_release, version_tuple


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

    def test_can_apply_is_recorded_when_enabled(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            service = CoreUpdateService(
                current_version="0.7.0",
                update_dir=Path(folder),
                can_apply=True,
            )
            service._set(status="ready", staged_path="MediaDownloaderCore.exe", can_apply=service.can_apply)
            self.assertTrue(service.snapshot()["can_apply"])

    def test_request_bytes_falls_back_to_curl_cffi_after_urllib_tls_errors(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as folder:
            service = CoreUpdateService(
                current_version="0.10.1",
                update_dir=Path(folder),
            )
            with (
                patch("hybrid_core.core_updater.urllib.request.urlopen", side_effect=OSError("SSL EOF")),
                patch.object(service, "_curl_cffi_bytes", return_value=b'{"ok":true}') as fallback,
            ):
                payload = service._request_bytes("https://api.github.com/test", timeout=1)

            self.assertEqual(payload, b'{"ok":true}')
            fallback.assert_called_once()

    def test_checksum_parser_matches_asset(self):
        digest = "a" * 64
        text = f"{digest}  MediaDownloaderCore.exe\n"
        self.assertEqual(_expected_checksum(text, "MediaDownloaderCore.exe"), digest)


if __name__ == "__main__":
    unittest.main()
