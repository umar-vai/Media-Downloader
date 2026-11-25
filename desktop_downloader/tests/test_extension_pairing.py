"""The connector discovers the app without exposing the installation pairing token."""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop_downloader"))

from extension_server import ExtensionServer

TOKEN = "pair-token-xyz"


def _get(port: int, path: str, origin: str = "") -> tuple[int, dict]:
    headers = {"Origin": origin} if origin else {}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class ExtensionPairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ExtensionServer(token=TOKEN, on_download=lambda _d: True, app_version="t", capture=False)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)

    def test_loopback_never_returns_the_pairing_token(self) -> None:
        for origin, expected in (("chrome-extension://abcdefghijklmnop", 404),
                                 ("https://evil.example", 403), ("", 404)):
            with self.subTest(origin=origin):
                status, body = _get(self.server.port, "/pair", origin)
                self.assertEqual(status, expected)
                self.assertNotIn("token", body)

    def test_ping_reports_the_capture_option_live(self) -> None:
        self.assertIs(_get(self.server.port, "/ping")[1]["capture"], False)
        self.server.capture = True
        self.assertIs(_get(self.server.port, "/ping")[1]["capture"], True)

    def test_authenticated_hello_marks_the_extension_connected(self) -> None:
        self.assertFalse(self.server.extension_active())
        _get(self.server.port, "/ping", "moz-extension://1234")
        self.assertFalse(self.server.extension_active())
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.port}/hello",
            headers={"Origin": "moz-extension://1234", "X-MD-Token": TOKEN},
            data=b"{}",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(self.server.extension_active())


if __name__ == "__main__":
    unittest.main()
