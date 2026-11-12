from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_downloader"))

from command_palette import filter_commands
from download_queue import DownloadQueue
from download_rules import match_rule, parse_rules, resolve_target
from extension_server import ExtensionServer, cookies_to_netscape
from media_core import options as engine_options


class RulesTests(unittest.TestCase):
    def test_parse_and_resolve(self) -> None:
        rules = parse_rules("# c\nexample.com => D:\\Vid | 1080p\nwww.other.com | 480p\nbad line without host =>")
        self.assertEqual(len(rules), 3)
        self.assertEqual(match_rule("https://www.example.com/x", rules).folder, "D:\\Vid")
        self.assertEqual(match_rule("https://sub.other.com/x", rules).quality, "480p")
        folder, quality = resolve_target(
            "https://example.com/v", rules=rules, default_dir=Path("C:/dl"), default_quality="720p"
        )
        self.assertEqual((str(folder), quality), ("D:\\Vid", "1080p"))
        folder, quality = resolve_target(
            "https://nomatch.org/v", rules=rules, default_dir=Path("C:/dl"),
            default_quality="720p", organize_by_site=True, platform="Nomatch",
        )
        self.assertEqual((folder.name, quality), ("Nomatch", "720p"))


class QueueRetryTests(unittest.TestCase):
    def test_auto_retry_cycle_and_throttle(self) -> None:
        queue = DownloadQueue(max_concurrent=6)
        item = queue.enqueue({"url": "https://e.com/a", "batch_id": "b", "request_settings": {}})
        queue.start_available()
        delay = queue.schedule_auto_retry(item.id, "HTTP Error 429: Too Many Requests")
        self.assertGreaterEqual(delay, 60)
        snap = queue.get(item.id)
        self.assertEqual(snap["status"], "queued")
        self.assertEqual(snap["auto_attempts"], 1)
        self.assertTrue(queue.throttled)
        self.assertEqual(queue.effective_concurrency(), 2)
        self.assertTrue(queue.release_auto_retry(item.id))
        self.assertEqual(len(queue.start_available()), 1)

    def test_single_download_is_not_auto_retried(self) -> None:
        queue = DownloadQueue()
        item = queue.enqueue({"url": "https://e.com/a"})
        queue.start_available()
        self.assertIsNone(queue.schedule_auto_retry(item.id, "boom"))

    def test_cancel_during_backoff_wins(self) -> None:
        queue = DownloadQueue()
        item = queue.enqueue({"url": "https://e.com/a", "batch_id": "b"})
        queue.start_available()
        queue.schedule_auto_retry(item.id, "boom")
        queue.request_cancel(item.id)
        self.assertFalse(queue.release_auto_retry(item.id))
        self.assertEqual(queue.get(item.id)["status"], "cancelled")

    def test_update_url_resets_cached_info(self) -> None:
        queue = DownloadQueue()
        item = queue.enqueue(
            {"url": "https://e.com/a", "cached_info": {"x": 1}, "request_settings": {"source_url": "old"}}
        )
        queue.start_available()
        queue.fail(item.id, "bad")
        self.assertTrue(queue.update_url(item.id, "https://e.com/b"))
        self.assertEqual(queue.get(item.id)["url"], "https://e.com/b")
        self.assertTrue(queue.retry(item.id))


class ExtensionServerTests(unittest.TestCase):
    def test_netscape_conversion(self) -> None:
        text = cookies_to_netscape([
            {"domain": ".a.com", "name": "s", "value": "1", "path": "/", "secure": True,
             "expirationDate": 2000000000, "hostOnly": False},
            {"domain": "b.com", "name": "x", "value": "bad\nvalue"},
        ])
        self.assertIn(".a.com\tTRUE\t/\tTRUE\t2000000000\ts\t1", text)
        self.assertNotIn("b.com", text)

    def test_token_and_origin_checks(self) -> None:
        received: list[dict] = []
        server = ExtensionServer(token="secret", on_download=lambda d: received.append(d) or True, app_version="t")
        self.assertTrue(server.start())
        try:
            base = f"http://127.0.0.1:{server.port}"
            with urllib.request.urlopen(base + "/ping") as response:
                self.assertTrue(json.load(response)["ok"])

            def post(token: str, origin: str = "chrome-extension://abc", url: str = "https://e.com/v"):
                request = urllib.request.Request(
                    base + "/download", data=json.dumps({"url": url}).encode(),
                    headers={"X-MD-Token": token, "Content-Type": "application/json", "Origin": origin},
                    method="POST",
                )
                return urllib.request.urlopen(request)

            with self.assertRaises(urllib.error.HTTPError) as bad:
                post("wrong")
            self.assertEqual(bad.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as web:
                post("secret", origin="https://evil.example")
            self.assertEqual(web.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as scheme:
                post("secret", url="file:///c:/x")
            self.assertEqual(scheme.exception.code, 400)
            with post("secret") as response:
                self.assertTrue(json.load(response)["ok"])
            self.assertEqual(received[0]["url"], "https://e.com/v")
        finally:
            server.stop()


class OptionsTests(unittest.TestCase):
    def tearDown(self) -> None:
        engine_options.reset()
        engine_options.clear_request_context()

    def test_cookie_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / "c.txt"
            jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            engine_options.configure(cookies_file=str(jar), cookies_browser="firefox")
            self.assertEqual(engine_options.cookie_options(), {"cookiefile": str(jar)})
            engine_options.set_request_context(cookiefile=str(jar), referer="https://r.com/")
            opts = engine_options.apply_request_options({"http_headers": {"A": "b"}})
            self.assertEqual(opts["http_headers"]["Referer"], "https://r.com/")
        engine_options.reset()
        engine_options.clear_request_context()
        engine_options.configure(cookies_browser="firefox")
        self.assertEqual(engine_options.cookie_options(), {"cookiesfrombrowser": ("firefox",)})

    def test_download_extras(self) -> None:
        engine_options.configure(subtitles="Embed in video", embed_thumbnail=True, embed_metadata=True)
        opts, processors = engine_options.download_extras("Video")
        self.assertTrue(opts["writesubtitles"])
        self.assertTrue(opts["writethumbnail"])
        keys = [p["key"] for p in processors]
        self.assertEqual(keys[0], "FFmpegMetadata")
        self.assertIn("FFmpegEmbedSubtitle", keys)
        opts, _processors = engine_options.download_extras("Audio")
        self.assertNotIn("writesubtitles", opts)

    def test_final_media_filter(self) -> None:
        self.assertFalse(engine_options.is_final_media(Path("a.jpg")))
        self.assertFalse(engine_options.is_final_media(Path("a.mp4.part")))
        self.assertTrue(engine_options.is_final_media(Path("a.mp4")))


class SkipExistingTests(unittest.TestCase):
    def test_existing_file_is_reused(self) -> None:
        import threading

        from media_core.engine import download_from_analysis

        engine_options.reset()
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "Song.mp4"
            existing.write_bytes(b"data")
            (Path(tmp) / "Song.jpg").write_bytes(b"thumb")
            result = download_from_analysis(
                url="https://example.com/v", cached_info={}, download_dir=Path(tmp), filename="Song",
                mode="Video", video_quality="720p", audio_format="mp3", audio_quality="192",
                cancel_event=threading.Event(),
            )
            self.assertEqual(result, existing)


class PaletteTests(unittest.TestCase):
    def test_filter(self) -> None:
        commands = [("Go to Batch", lambda: None), ("Open settings", lambda: None)]
        self.assertEqual([c[0] for c in filter_commands(commands, "set")], ["Open settings"])
        self.assertEqual(len(filter_commands(commands, "")), 2)


if __name__ == "__main__":
    unittest.main()
