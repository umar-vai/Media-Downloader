from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update_manager import (
    GITHUB_WEB_BASE,
    UpdateError,
    _headers_for_url,
    _release_from_payload,
    _release_from_redirect_result,
    _release_from_web_html,
    _select_best_release,
    is_newer_version,
    normalize_version,
    parse_checksum,
    sha256_file,
)


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
            path.write_bytes(b"media-downloader")
            expected = hashlib.sha256(b"media-downloader").hexdigest()
            self.assertEqual(sha256_file(path), expected)

    def test_api_headers_are_only_used_for_api_github(self) -> None:
        api_headers = _headers_for_url("https://api.github.com/repos/umar-vai/Media-Downloader/releases/latest")
        self.assertEqual(api_headers["Accept"], "application/vnd.github+json")
        self.assertIn("X-GitHub-Api-Version", api_headers)

        web_headers = _headers_for_url(f"{GITHUB_WEB_BASE}/releases/latest/download/MediaDownloader.exe")
        self.assertEqual(web_headers["Accept"], "*/*")
        self.assertNotIn("X-GitHub-Api-Version", web_headers)

    def test_binary_accept_header(self) -> None:
        headers = _headers_for_url(
            f"{GITHUB_WEB_BASE}/releases/download/v2.3.8/MediaDownloader.exe",
            accept="application/octet-stream",
        )
        self.assertEqual(headers["Accept"], "application/octet-stream")

    def test_release_payload_parser(self) -> None:
        digest_url = f"{GITHUB_WEB_BASE}/releases/download/v2.3.8/MediaDownloader.exe.sha256"
        exe_url = f"{GITHUB_WEB_BASE}/releases/download/v2.3.8/MediaDownloader.exe"
        release = _release_from_payload(
            {
                "tag_name": "v2.3.8",
                "body": "Updater reliability",
                "html_url": f"{GITHUB_WEB_BASE}/releases/tag/v2.3.8",
                "assets": [
                    {"name": "MediaDownloader.exe", "browser_download_url": exe_url},
                    {"name": "MediaDownloader.exe.sha256", "browser_download_url": digest_url},
                ],
            }
        )
        self.assertEqual(release.version, "2.3.8")
        self.assertEqual(release.asset_url, exe_url)
        self.assertEqual(release.checksum_url, digest_url)


    def test_select_best_release_ignores_malformed_and_old_entries(self) -> None:
        def payload(tag: str) -> dict:
            return {
                "tag_name": tag,
                "body": "",
                "html_url": f"{GITHUB_WEB_BASE}/releases/tag/{tag}",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "MediaDownloader.exe",
                        "browser_download_url": f"{GITHUB_WEB_BASE}/releases/download/{tag}/MediaDownloader.exe",
                    },
                    {
                        "name": "MediaDownloader.exe.sha256",
                        "browser_download_url": f"{GITHUB_WEB_BASE}/releases/download/{tag}/MediaDownloader.exe.sha256",
                    },
                ],
            }

        malformed = payload("v")
        old = payload("v2.4.0")
        latest = payload("v2.5.1")
        prerelease = payload("v9.0.0")
        prerelease["prerelease"] = True

        release = _select_best_release([malformed, old, prerelease, latest])
        self.assertEqual(release.version, "2.5.1")

    def test_release_page_parser_chooses_highest_semver(self) -> None:
        html = """
        <a href="/umar-vai/Media-Downloader/releases/tag/v">bad</a>
        <a href="/umar-vai/Media-Downloader/releases/tag/v2.4.0">old</a>
        <a href="/umar-vai/Media-Downloader/releases/tag/v2.5.1">new</a>
        """
        release = _release_from_web_html(html)
        self.assertEqual(release.version, "2.5.1")
        self.assertTrue(release.asset_url.endswith("/v2.5.1/MediaDownloader.exe"))

    def test_redirect_release_parser(self) -> None:
        digest = "b" * 64
        release = _release_from_redirect_result(
            f"{GITHUB_WEB_BASE}/releases/download/v2.3.8/MediaDownloader.exe.sha256",
            f"{digest}  MediaDownloader.exe",
        )
        self.assertEqual(release.version, "2.3.8")
        self.assertTrue(release.asset_url.endswith("/v2.3.8/MediaDownloader.exe"))


if __name__ == "__main__":
    unittest.main()
