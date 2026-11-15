"""The browser link pairs itself (like IDM/NDM) and never needs a token copied by hand."""

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

    def test_extension_can_pair_itself(self) -> None:
        status, body = _get(self.server.port, "/pair", "chrome-extension://abcdefghijklmnop")
        self.assertEqual((status, body.get("token")), (200, TOKEN))
        self.assertTrue(self.server.extension_active())

    def test_web_pages_and_plain_tools_cannot_read_the_token(self) -> None:
        self.assertEqual(_get(self.server.port, "/pair", "https://evil.example")[0], 403)
        status, body = _get(self.server.port, "/pair")
        self.assertEqual(status, 403)
        self.assertNotIn("token", body)

    def test_ping_reports_the_capture_option_live(self) -> None:
        self.assertIs(_get(self.server.port, "/ping")[1]["capture"], False)
        self.server.capture = True
        self.assertIs(_get(self.server.port, "/ping")[1]["capture"], True)

    def test_ping_from_extension_marks_it_connected(self) -> None:
        self.assertFalse(self.server.extension_active())
        _get(self.server.port, "/ping", "moz-extension://1234")
        self.assertTrue(self.server.extension_active())


if __name__ == "__main__":
    unittest.main()
