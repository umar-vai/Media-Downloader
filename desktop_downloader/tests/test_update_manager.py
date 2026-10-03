from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update_manager import UpdateError, is_newer_version, normalize_version, parse_checksum, sha256_file


class VersionTests(unittest.TestCase):
    def test_normalize_version(self) -> None:
        self.assertEqual(normalize_version("v2.2.0"), (2, 2, 0))
        self.assertEqual(normalize_version("2.1"), (2, 1, 0))
        self.assertEqual(normalize_version("3"), (3, 0, 0))

    def test_version_comparison(self) -> None:
        self.assertTrue(is_newer_version("2.2.0", "2.1.0"))
        self.assertFalse(is_newer_version("2.1.0", "2.1.0"))
        self.assertFalse(is_newer_version("2.0.9", "2.1.0"))

    def test_checksum_parser(self) -> None:
        digest = "a" * 64
        self.assertEqual(parse_checksum(f"{digest}  MediaDownloader.exe"), digest)
        with self.assertRaises(UpdateError):
            parse_checksum("not-a-checksum")

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"team-fahad")
            expected = hashlib.sha256(b"team-fahad").hexdigest()
            self.assertEqual(sha256_file(path), expected)


if __name__ == "__main__":
    unittest.main()
