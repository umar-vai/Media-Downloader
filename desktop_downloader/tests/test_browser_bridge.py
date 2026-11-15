"""Links that only play in a real browser: the app opens them in the user's browser and the
extension reports the player's stream back (with the player's Referer/Origin)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_downloader"))

import browser_extension
from extension_server import ExtensionServer, resolve_page_url
from media_core import engine
from media_core import options as engine_options

TOKEN = "test-token-123"


def _post(port: int, path: str, body: dict, token: str = TOKEN) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-MD-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class BrowserBridgeServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ExtensionServer(token=TOKEN, on_download=lambda _data: True, app_version="test")
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)

    def test_marker_is_a_fragment(self) -> None:
        self.assertEqual(resolve_page_url("https://a.example/ep-4", "ab" * 12), "https://a.example/ep-4#md-resolve=" + "ab" * 12)
        self.assertEqual(resolve_page_url("https://a.example/#x", "ab" * 12), "https://a.example/#x&md-resolve=" + "ab" * 12)

    def test_not_used_until_the_extension_says_hello(self) -> None:
        opened: list[str] = []
        self.assertIsNone(self.server.resolve_in_browser("https://a.example/ep", opener=opened.append, timeout=1))
        self.assertEqual(opened, [])
        self.assertEqual(_post(self.server.port, "/hello", {}, token="wrong")[0], 401)
        self.assertFalse(self.server.extension_connected)
        self.assertEqual(_post(self.server.port, "/hello", {})[0], 200)
        self.assertTrue(self.server.extension_connected)

    def test_round_trip_returns_the_extension_report(self) -> None:
        _post(self.server.port, "/hello", {})
        stream = {"url": "https://cdn.example/master.m3u8", "kind": "hls", "master": True,
                  "referer": "https://player.example/embed/1", "headers": {"Origin": "https://player.example"}}

        def fake_browser(url: str) -> None:
            job = re.search(r"md-resolve=([0-9a-f]+)", url).group(1)
            threading.Timer(0.2, lambda: _post(self.server.port, "/resolved", {"id": job, "title": "Ep 4", "streams": [stream]})).start()

        report = self.server.resolve_in_browser("https://a.example/ep-4", opener=fake_browser, timeout=5)
        self.assertEqual(report["streams"][0]["url"], stream["url"])
        self.assertEqual(report["title"], "Ep 4")

    def test_unknown_job_is_rejected_and_cancel_stops_waiting(self) -> None:
        _post(self.server.port, "/hello", {})
        self.assertEqual(_post(self.server.port, "/resolved", {"id": "f" * 24, "streams": []})[0], 404)
        cancel = threading.Event()
        cancel.set()
        self.assertIsNone(self.server.resolve_in_browser("https://a.example/ep", opener=lambda _u: None, cancel_event=cancel, timeout=5))


class AnalyzeEndpointTests(unittest.TestCase):
    """The extension asks the app which qualities a video link (reel, post, watch page) has."""

    def test_analyze_route_returns_the_app_answer(self) -> None:
        seen: list[dict] = []

        def on_analyze(data: dict) -> dict:
            seen.append(data)
            return {"ok": True, "title": "Reel", "qualities": ["1080p", "720p"]}

        server = ExtensionServer(token=TOKEN, on_download=lambda _d: True, app_version="t", on_analyze=on_analyze)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        status, body = _post(server.port, "/analyze", {"url": "https://www.facebook.com/reel/1", "cookies": []})
        self.assertEqual(status, 200)
        self.assertEqual(body["qualities"], ["1080p", "720p"])
        self.assertEqual(seen[0]["url"], "https://www.facebook.com/reel/1")
        self.assertEqual(_post(server.port, "/analyze", {"url": "javascript:alert(1)"})[0], 400)
        self.assertEqual(_post(server.port, "/analyze", {"url": "https://x.com/a"}, token="bad")[0], 401)

    def test_app_lookup_uses_browser_cookies_and_never_opens_a_tab(self) -> None:
        import app as app_module

        captured: dict = {}

        def fake_analyze(url, cancel_event):
            captured["cookiefile"] = engine_options.cookie_options().get("cookiefile", "")
            captured["bridge"] = engine_options.browser_bridge()
            captured["cookie_text"] = Path(captured["cookiefile"]).read_text(encoding="utf-8")
            return {"title": "Reel", "duration": 12.0, "platform": "Facebook",
                    "qualities": ["360p", "720p", "1080p", "Best available"]}, {}

        engine_options.configure(browser_bridge=lambda *_a: {"streams": []})
        self.addCleanup(lambda: engine_options.configure(browser_bridge=None))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(app_module, "EXT_COOKIE_DIR", Path(tmp)), \
                mock.patch.object(app_module, "shared_analyze_url", side_effect=fake_analyze):
            result = app_module.DownloaderApp._analyze_for_extension(None, {
                "url": "https://www.facebook.com/reel/1", "pageUrl": "https://www.facebook.com/reel/1",
                "cookies": [{"domain": ".facebook.com", "name": "c_user", "value": "1", "hostOnly": False}],
            })
            left = list(Path(tmp).iterdir())
        self.assertEqual(result["qualities"], ["1080p", "720p", "360p"])
        self.assertIn("c_user", captured["cookie_text"])
        self.assertIsNone(captured["bridge"])
        self.assertEqual(left, [], "the temporary cookie file is removed")
        self.assertIsNotNone(engine_options.browser_bridge())


class AnalyzeUsesBrowserBridgeTests(unittest.TestCase):
    def tearDown(self) -> None:
        engine_options.configure(browser_bridge=None)

    def test_failed_link_is_resolved_through_the_browser(self) -> None:
        report = {"title": "Horror Collector - Episode 4", "streams": [
            {"url": "https://cdn.example/master.m3u8", "kind": "hls", "master": True,
             "referer": "https://player.example/embed/1",
             "headers": {"Origin": "https://player.example", "Cookie": "a=1", "X-Other": "dropped"}},
        ]}
        engine_options.configure(browser_bridge=lambda url, cancel_event: report)
        expanded = {"title": "master", "formats": [
            {"format_id": "1080", "height": 1080, "vcodec": "avc1", "acodec": "none", "url": "https://cdn.example/1080.m3u8"},
            {"format_id": "audio", "vcodec": "none", "url": "https://cdn.example/a.m3u8"},
        ]}
        seen_headers: list[dict] = []

        class FakeYDL:
            def __init__(self, params):
                self.params = params

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, url, download=False, force_generic_extractor=False):
                if url.endswith(".m3u8"):
                    seen_headers.append(dict(self.params.get("http_headers") or {}))
                    return dict(expanded)
                raise RuntimeError("Unsupported URL")

        with mock.patch.object(engine.yt_dlp, "YoutubeDL", FakeYDL), \
                mock.patch.object(engine, "resolve_with_installed_browser", side_effect=AssertionError("not needed")):
            summary, info = engine.analyze_url("https://anime.example/watch/ep-4", cancel_event=threading.Event())

        self.assertEqual(summary["title"], "Horror Collector - Episode 4")
        self.assertIn("1080p", summary["qualities"])
        self.assertEqual(info["webpage_url"], "https://anime.example/watch/ep-4")
        self.assertEqual(seen_headers[0]["Referer"], "https://player.example/embed/1")
        self.assertEqual(seen_headers[0]["Origin"], "https://player.example")
        self.assertNotIn("X-Other", seen_headers[0])


class ExtensionSourceTests(unittest.TestCase):
    def test_extension_can_read_player_playlists_and_answer_the_app(self) -> None:
        manifest = browser_extension.MANIFEST
        self.assertIn("declarativeNetRequestWithHostAccess", manifest["permissions"])
        self.assertIn("webNavigation", manifest["permissions"])
        bg = browser_extension.BACKGROUND_JS
        self.assertIn("withPlayerHeaders", bg)
        self.assertIn('"/resolved"', bg)
        self.assertIn('"/hello"', bg)
        self.assertIn("md-resolve=", bg)
        content_js = browser_extension.CONTENT_JS
        self.assertIn("content.fetch", content_js)
        self.assertIn("md-autoplay", content_js)
        # Universal sites: each video's own link is looked up by the app.
        self.assertIn("function videoLink(el)", content_js)
        self.assertIn('"md-analyze"', content_js)
        self.assertIn('"/analyze"', bg)
        self.assertNotIn("No stream captured yet", content_js)


if __name__ == "__main__":
    unittest.main()
