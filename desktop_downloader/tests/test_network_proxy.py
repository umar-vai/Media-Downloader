from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from network_proxy import (
    ProxyRoute,
    _normalize_proxy_url,
    _parse_windows_proxy_server,
    active_proxy_route,
    safe_proxy_label,
    yt_dlp_proxy_options,
)


class NetworkProxyTests(unittest.TestCase):
    def test_normalizes_plain_http_proxy(self) -> None:
        self.assertEqual(
            _normalize_proxy_url("127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_parses_windows_multi_protocol_proxy(self) -> None:
        value = "http=127.0.0.1:8080;https=127.0.0.1:8443;socks=127.0.0.1:1080"
        self.assertEqual(
            _parse_windows_proxy_server(value),
            "http://127.0.0.1:8443",
        )

    def test_parses_windows_socks_proxy(self) -> None:
        self.assertEqual(
            _parse_windows_proxy_server("socks=127.0.0.1:1080"),
            "socks5://127.0.0.1:1080",
        )

    @patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890"}, clear=True)
    def test_environment_proxy_is_forwarded_to_ytdlp(self) -> None:
        with patch("network_proxy._windows_registry_proxy", return_value=None):
            self.assertEqual(
                active_proxy_route(),
                ProxyRoute("http://127.0.0.1:7890", "environment"),
            )
            self.assertEqual(
                yt_dlp_proxy_options(),
                {"proxy": "http://127.0.0.1:7890"},
            )

    @patch("network_proxy.active_proxy_route", return_value=ProxyRoute("http://127.0.0.1:7890", "windows"))
    def test_safe_label_does_not_expose_credentials(self, _route) -> None:
        self.assertEqual(safe_proxy_label(), "System proxy active • 127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
