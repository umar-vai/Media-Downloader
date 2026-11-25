"""IDM-style history, website folders and the settings/history pages."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_downloader"))

import app as app_module
import browser_extension
from download_rules import parse_rules, resolve_target
from history_store import HistoryStore, make_history_entry
from history_window import entry_state, matches, property_rows
from media_core import options as engine_options
from media_probe import parse_ffmpeg_output
from options_dialog import rules_from_rows

FFMPEG_SAMPLE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'ep4.mp4':
  Duration: 00:25:03.12, start: 0.000000, bitrate: 3456 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 3300 kb/s, 23.98 fps
  Stream #0:1[0x2](eng): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s
"""


class HistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.store = HistoryStore(self.dir / "history.json")

    def test_completed_download_keeps_full_details(self) -> None:
        media = self.dir / "Episode 4.mp4"
        media.write_bytes(b"x" * 4000)
        now = time.time()
        entry = make_history_entry(
            media, title="Episode 4", source_url="https://cdn.x/master.m3u8", platform="generic",
            started_at=now - 20, finished_at=now, page_url="https://anime.example/watch/ep-4",
            resolution="1920x1080", video_quality="1080p", http_headers={"Origin": "https://player.x"},
        )
        saved = self.store.add(entry)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["website"], "anime.example")
        self.assertEqual(saved["elapsed_seconds"], 20)
        self.assertEqual(saved["avg_speed_bps"], 200)
        self.assertEqual(saved["extension"], "MP4")
        self.assertEqual(saved["download_dir"], str(self.dir))
        stored = self.store.get(saved["id"])
        self.assertEqual(stored["resolution"], "1920x1080")
        self.assertEqual(stored["video_quality"], "1080p")
        self.assertEqual(stored["http_headers"], {"Origin": "https://player.x"})
        self.assertEqual(entry_state(stored), "Completed")

    def test_failed_download_is_kept_and_replaced_by_its_success(self) -> None:
        failed = self.store.add(make_history_entry(
            None, status="failed", title="Clip", source_url="https://e.com/v.m3u8", platform="generic",
            error="HTTP Error 403"))
        self.assertEqual(entry_state(failed), "Failed")
        self.assertEqual(failed["file_path"], "")
        media = self.dir / "Clip.mp4"
        media.write_bytes(b"1")
        self.store.add(make_history_entry(media, title="Clip", source_url="https://e.com/v.m3u8", platform="generic"))
        items = self.store.list()
        self.assertEqual([item["status"] for item in items], ["completed"])

    def test_old_history_files_still_load(self) -> None:
        (self.dir / "history.json").write_text(json.dumps({"version": 1, "items": [{
            "id": "a1", "created_at": "2026-01-01T00:00:00+00:00", "title": "Old", "file_path": str(self.dir / "old.mp4"),
            "source_url": "https://www.youtube.com/watch?v=1", "platform": "youtube", "mode": "Video",
        }]}), encoding="utf-8")
        item = self.store.list()[0]
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["website"], "youtube.com")
        self.assertEqual(entry_state(item), "Missing")
        self.assertEqual(self.store.prune_missing(), 1)


class HistoryPageLogicTests(unittest.TestCase):
    def test_filters_and_search(self) -> None:
        done = {"status": "completed", "file_path": __file__, "mode": "Video", "title": "Horror Collector",
                "website": "animewave.to"}
        failed = {"status": "failed", "file_path": "", "mode": "Audio", "title": "Song", "website": "youtube.com"}
        self.assertTrue(matches(done, "Completed", "horror anime"))
        self.assertFalse(matches(done, "Failed", ""))
        self.assertTrue(matches(failed, "Failed", ""))
        self.assertTrue(matches(failed, "Audio", "youtube"))
        self.assertFalse(matches(failed, "Video", ""))

    def test_properties_rows(self) -> None:
        rows = dict(property_rows({
            "status": "failed", "title": "Clip", "error": "HTTP Error 403", "page_url": "https://a.example/p",
            "download_url": "https://cdn.example/m.m3u8", "file_size": 2048, "avg_speed_bps": 1024,
            "elapsed_seconds": 75, "started_at": "2026-11-15T07:00:00+00:00",
        }))
        self.assertEqual(rows["Status"], "Failed")
        self.assertEqual(rows["Error"], "HTTP Error 403")
        self.assertEqual(rows["Web page"], "https://a.example/p")
        self.assertEqual(rows["Download link"], "https://cdn.example/m.m3u8")
        self.assertEqual(rows["Time taken"], "1:15")
        self.assertEqual(rows["Average speed"], "1.0 KB/s")
        self.assertIn("2,048 bytes", rows["Size"])

    def test_ffmpeg_probe_parsing(self) -> None:
        info = parse_ffmpeg_output(FFMPEG_SAMPLE)
        self.assertEqual(info["resolution"], "1920x1080")
        self.assertEqual(info["video_codec"], "h264")
        self.assertEqual(info["audio_codec"], "aac")
        self.assertEqual(info["duration_seconds"], 1503)
        self.assertEqual(info["bitrate_kbps"], 3456)


class WebsiteFolderTests(unittest.TestCase):
    def test_table_rows_round_trip_to_rules(self) -> None:
        text = rules_from_rows([
            ("animewave.to", r"H:\Anime", "1080p"),
            ("youtube.com", r"D:\YouTube", "Default"),
            ("only-quality.com", "", "480p"),
            ("", r"D:\Lost", "1080p"),
            ("nothing.com", "", "Default"),
        ])
        rules = parse_rules(text)
        self.assertEqual([(r.host, r.folder, r.quality) for r in rules], [
            ("animewave.to", r"H:\Anime", "1080p"),
            ("youtube.com", r"D:\YouTube", ""),
            ("only-quality.com", "", "480p"),
        ])
        folder, quality = resolve_target("https://animewave.to/watch/x", rules=rules,
                                         default_dir=Path(r"D:\Downloads"), default_quality="720p")
        self.assertEqual((folder, quality), (Path(r"H:\Anime"), "1080p"))

    def test_extension_stream_uses_the_page_website_for_rules(self) -> None:
        queued: list[list[dict]] = []
        with tempfile.TemporaryDirectory() as tmp:
            fake = SimpleNamespace(
                settings={"folder_rules": f"animewave.to => {Path(tmp) / 'Anime'} | 1080p"},
                download_dir=Path(tmp) / "default",
                download_queue=SimpleNamespace(enqueue_batch=queued.append),
                _set_status=lambda *a, **k: None,
                _schedule_download_activity_refresh=lambda *a: None,
                _start_available_downloads=lambda: None,
            )
            app_module.DownloaderApp._enqueue_batch_downloads(
                fake, "https://fetch.cdn.top/hls/master.m3u8",
                {"mode": "Video", "video_quality": "720p", "audio_format": "MP3", "audio_quality": "192"},
                extra={"page_url": "https://animewave.to/watch/ep-4"},
            )
        payload = queued[0][0]
        self.assertEqual(payload["download_dir"], str(Path(tmp) / "Anime"))
        self.assertEqual(payload["request_settings"]["video_quality"], "1080p")
        self.assertEqual(payload["request_settings"]["page_url"], "https://animewave.to/watch/ep-4")


    def test_folder_override_does_not_touch_an_unusable_default(self) -> None:
        queued = []
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            default = base / "unavailable-default"
            default.write_text("a file, not a directory")
            target = base / "chosen"
            fake = SimpleNamespace(
                settings={}, download_dir=default,
                download_queue=SimpleNamespace(enqueue_batch=queued.append),
                _set_status=lambda *a, **k: None,
                _schedule_download_activity_refresh=lambda *a: None,
                _start_available_downloads=lambda: None,
            )
            app_module.DownloaderApp._enqueue_batch_downloads(
                fake, "https://example.com/test.zip", {}, target_dir_override=target,
            )
            self.assertEqual(queued[0][0]["download_dir"], str(target))
            self.assertEqual(list(target.iterdir()), [])


class RedownloadTests(unittest.TestCase):
    def test_skip_existing_can_be_forced_off_per_download(self) -> None:
        engine_options.configure(skip_existing=True)
        self.addCleanup(engine_options.clear_request_context)
        self.assertTrue(engine_options.skip_existing())
        engine_options.set_request_context(skip_existing=False)
        self.assertFalse(engine_options.skip_existing())
        engine_options.clear_request_context()
        self.assertTrue(engine_options.skip_existing())

    def test_history_redownload_queues_the_same_link_and_settings(self) -> None:
        calls: list[tuple] = []
        fake = SimpleNamespace(
            _default_batch_options=lambda mode=None: {"mode": mode or "Video", "video_quality": "720p",
                                                       "audio_format": "MP3", "audio_quality": "192"},
            _enqueue_batch_downloads=lambda text, options, extra=None: calls.append((text, options, extra)),
            _set_status=lambda *a, **k: None,
            show_page=lambda name: None,
        )
        entry = {"download_url": "https://cdn.x/master.m3u8", "mode": "Video", "video_quality": "1080p",
                 "title": "Ep 4", "page_url": "https://anime.example/ep-4", "referer": "https://player.x/e/1",
                 "http_headers": {"Origin": "https://player.x"}}
        app_module.DownloaderApp.redownload_from_history(fake, entry, resume=False)
        app_module.DownloaderApp.redownload_from_history(fake, entry, resume=True)
        (url, options, extra), (_u, _o, resume_extra) = calls
        self.assertEqual(url, "https://cdn.x/master.m3u8")
        self.assertEqual(options["video_quality"], "1080p")
        self.assertEqual(extra["referer"], "https://player.x/e/1")
        self.assertEqual(extra["page_url"], "https://anime.example/ep-4")
        self.assertTrue(extra["force_redownload"])
        self.assertNotIn("force_redownload", resume_extra)


class ExtensionCurrentPageTests(unittest.TestCase):
    def test_menu_lists_only_the_current_page(self) -> None:
        bg = browser_extension.BACKGROUND_JS
        self.assertIn("function currentStreams(state)", bg)
        self.assertNotIn("From the previous page", bg)
        self.assertIn("pageUrl: payload.pageUrl", bg)


if __name__ == "__main__":
    unittest.main()
