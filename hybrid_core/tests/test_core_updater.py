from __future__ import annotations

import unittest

from hybrid_core.core_updater import CoreUpdateService, _expected_checksum, _sha256_file, select_release, version_tuple


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

    def test_urllib_stream_resumes_existing_partial(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        full = b"abcdef"
        class FakeResponse:
            status = 206
            headers = {"Content-Length": "3"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def getcode(self): return self.status
            def read(self, size=-1):
                if hasattr(self, "_done"):
                    return b""
                self._done = True
                return b"def"

        with TemporaryDirectory() as folder:
            root = Path(folder)
            service = CoreUpdateService(current_version="0.10.3", update_dir=root)
            partial = root / "MediaDownloaderCore.exe.part"
            partial.write_bytes(b"abc")

            with patch("hybrid_core.core_updater.urllib.request.urlopen", return_value=FakeResponse()) as mocked:
                service._urllib_stream_resume(
                    url="https://example.com/core.exe",
                    partial=partial,
                    expected_size=len(full),
                    report_progress=False,
                )

            request = mocked.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=3-")
            self.assertEqual(partial.read_bytes(), full)

    def test_sha256_file_matches_bytes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import hashlib

        with TemporaryDirectory() as folder:
            path = Path(folder) / "asset.bin"
            path.write_bytes(b"resume-me")
            self.assertEqual(_sha256_file(path), hashlib.sha256(b"resume-me").hexdigest())

    def test_checksum_parser_matches_asset(self):
        digest = "a" * 64
        text = f"{digest}  MediaDownloaderCore.exe\n"
        self.assertEqual(_expected_checksum(text, "MediaDownloaderCore.exe"), digest)


if __name__ == "__main__":
    unittest.main()
