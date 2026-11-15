from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import download_list as dl
from download_queue import DownloadQueue
from history_store import HistoryStore, make_history_entry


def snap(**kw):
    base = {"id": "r1", "name": "Link 1 • example.com", "url": "https://example.com/v", "status": "queued",
            "progress": 0.0, "detail": "", "error": "", "result_path": "", "created_at": 100.0,
            "started_at": 0.0, "finished_at": 0.0, "batch_id": "", "auto_attempts": 0, "mode": "Video"}
    base.update(kw)
    return base


def hist(**kw):
    base = {"id": "h1", "status": "completed", "file_path": "", "file_name": "clip.mp4", "title": "Clip",
            "mode": "Video", "file_size": 2048, "created_at": "2026-11-01T10:00:00+00:00",
            "started_at": "2026-11-01T09:59:00+00:00", "finished_at": "2026-11-01T10:00:00+00:00",
            "source_url": "https://www.youtube.com/watch?v=x", "request_id": ""}
    base.update(kw)
    return base


class CategoryTests(unittest.TestCase):
    def test_extension_decides_category(self) -> None:
        self.assertEqual(dl.category_for("a.MP4"), "video")
        self.assertEqual(dl.category_for("song.flac"), "audio")
        self.assertEqual(dl.category_for("pack.7z"), "compressed")
        self.assertEqual(dl.category_for("report.pdf"), "documents")
        self.assertEqual(dl.category_for("setup.exe"), "programs")
        self.assertEqual(dl.category_for("weird.xyz"), "misc")

    def test_mode_is_used_until_the_file_has_an_extension(self) -> None:
        self.assertEqual(dl.category_for("Link 1 • youtube.com", "Audio"), "audio")
        self.assertEqual(dl.category_for("Some title", "Video"), "video")
        self.assertEqual(dl.category_for("Some title", ""), "misc")
        # a dot inside a title is not an extension
        self.assertEqual(dl.category_for("Episode 4. The end", "Video"), "video")


class FormatTests(unittest.TestCase):
    def test_sizes_speeds_and_eta(self) -> None:
        self.assertEqual(dl.format_size(0), "")
        self.assertEqual(dl.format_size(512), "512 B")
        self.assertEqual(dl.format_size(134645), "131.49 KB")
        self.assertEqual(dl.format_size(25.69 * 1024 * 1024), "25.69 MB")
        self.assertEqual(dl.format_speed(1024 * 1024), "1.00 MB/s")
        self.assertEqual(dl.format_eta(45), "45 sec")
        self.assertEqual(dl.format_eta(125), "2 min 05 sec")
        self.assertEqual(dl.format_eta(3 * 3600 + 60), "3 hr 01 min")
        self.assertEqual(dl.format_eta(None), "")


class RowTests(unittest.TestCase):
    def test_running_row_shows_percent_speed_and_eta(self) -> None:
        row = dl.queue_row(snap(status="running", progress=0.4235, started_at=110.0),
                           {"total_bytes": 10_000_000, "downloaded_bytes": 4_235_000, "speed_bps": 2 * 1024 * 1024,
                            "eta_seconds": 75})
        self.assertEqual(row["status"], "downloading")
        self.assertEqual(row["status_text"], "42.4%")
        self.assertEqual(row["size"], 10_000_000)
        self.assertEqual(row["speed_text"], "2.00 MB/s")
        self.assertEqual(row["eta_text"], "1 min 15 sec")
        self.assertEqual(row["category"], "video")
        self.assertEqual(row["website"], "example.com")

    def test_running_without_progress_is_connecting(self) -> None:
        row = dl.queue_row(snap(status="running"))
        self.assertEqual((row["status"], row["status_text"]), ("connecting", "Connecting…"))

    def test_paused_and_retrying_rows(self) -> None:
        self.assertEqual(dl.queue_row(snap(status="paused", progress=0.5))["status_text"], "Paused  50%")
        retry = dl.queue_row(snap(status="queued", auto_attempts=2))
        self.assertEqual((retry["status"], retry["has_details"]), ("retrying", True))
        self.assertTrue(dl.queue_row(snap(status="failed", error="boom"))["has_details"])

    def test_merge_and_other_running_phases_are_named(self) -> None:
        merging = dl.queue_row(snap(status="running", progress=0.985, detail="Merging video and audio…"),
                               {"speed_bps": 9_000_000, "eta_seconds": 0})
        self.assertEqual((merging["status"], merging["status_text"], merging["speed_text"]),
                         ("finishing", "Merging…", ""))
        self.assertIn("stop", dl.available_actions([merging]))
        self.assertEqual(dl.queue_row(snap(status="running", progress=0.99, detail="Finalizing file…"))["status_text"],
                         "Finishing…")
        self.assertEqual(dl.queue_row(snap(status="running", detail="Analyzing batch link…"))["status_text"],
                         "Analyzing…")
        self.assertEqual(dl.queue_row(snap(status="running", progress=0.4, detail="2.0 MB/s"))["status_text"], "40.0%")
        self.assertEqual(dl.totals([merging])["downloading"], 1)

    def test_speed_is_hidden_once_not_running(self) -> None:
        row = dl.queue_row(snap(status="paused", progress=0.2), {"speed_bps": 5000, "eta_seconds": 9})
        self.assertEqual((row["speed_text"], row["eta_text"]), ("", ""))

    def test_history_row_marks_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"x")
            present = dl.history_row(hist(file_path=str(path)))
            gone = dl.history_row(hist(file_path=str(Path(tmp) / "gone.mp4")))
        self.assertEqual((present["status"], present["status_text"]), ("complete", "Complete"))
        self.assertTrue(present["file_exists"])
        self.assertEqual((gone["status"], gone["status_text"]), ("missing", "File missing"))
        self.assertEqual(present["website"], "youtube.com")
        self.assertEqual(present["size_text"], "2.00 KB")

    def test_merge_prefers_history_for_finished_and_queue_for_live(self) -> None:
        finished_q = dl.queue_row(snap(id="a", status="completed", result_path="x.mp4"))
        finished_h = dl.history_row(hist(id="ha", request_id="a"), exists=True)
        retried_q = dl.queue_row(snap(id="b", status="queued"))
        retried_h = dl.history_row(hist(id="hb", request_id="b", status="failed"), exists=False)
        cancelled_q = dl.queue_row(snap(id="c", status="cancelled"))  # never recorded in history
        old_h = dl.history_row(hist(id="hold"), exists=True)
        rows = dl.merge_rows([finished_q, retried_q, cancelled_q], [finished_h, retried_h, old_h])
        self.assertEqual(sorted(row["key"] for row in rows), ["h:ha", "h:hold", "q:b", "q:c"])


class FilterSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            dl.queue_row(snap(id="run", status="running", progress=0.3, created_at=300, batch_id="B1")),
            dl.queue_row(snap(id="wait", status="queued", created_at=200, batch_id="B1", mode="Audio")),
            dl.queue_row(snap(id="fail", status="failed", created_at=150)),
            dl.history_row(hist(id="zip", file_name="pack.zip", file_path="C:/dl/pack.zip", file_size=99), exists=True),
            dl.history_row(hist(id="gone", file_name="old.mp3", mode="Audio"), exists=False),
        ]

    def test_categories(self) -> None:
        keys = lambda cat: {row["key"] for row in dl.filter_rows(self.rows, cat)}  # noqa: E731
        self.assertEqual(len(dl.filter_rows(self.rows, "all")), 5)
        self.assertEqual(keys("audio"), {"q:wait", "h:gone"})
        self.assertEqual(keys("compressed"), {"h:zip"})
        self.assertEqual(keys("unfinished"), {"q:run", "q:wait", "q:fail"})
        self.assertEqual(keys("finished"), {"h:zip", "h:gone"})
        self.assertEqual(keys("queue:active"), {"q:run", "q:wait"})
        self.assertEqual(keys("queue:B1"), {"q:run", "q:wait"})
        counts = dl.category_counts(self.rows)
        self.assertEqual((counts["all"], counts["unfinished"], counts["finished"], counts["video"]), (5, 3, 2, 2))

    def test_search_matches_every_word(self) -> None:
        self.assertEqual({r["key"] for r in dl.filter_rows(self.rows, "all", "pack")}, {"h:zip"})
        self.assertEqual(dl.filter_rows(self.rows, "all", "pack missing"), [])
        self.assertEqual({r["key"] for r in dl.filter_rows(self.rows, "finished", "FILE missing")}, {"h:gone"})

    def test_sorting(self) -> None:
        by_size = [row["key"] for row in dl.sort_rows(self.rows, "size", descending=True)]
        self.assertEqual(by_size[:2], ["h:gone", "h:zip"])  # 2048 > 99
        by_status = [row["key"] for row in dl.sort_rows(self.rows, "status", descending=False)]
        self.assertEqual(by_status[0], "q:run")
        self.assertEqual(by_status[-1], "h:zip")
        newest = [row["key"] for row in dl.sort_rows(self.rows, "unknown-column")]
        self.assertEqual(newest[0], "h:zip")  # falls back to Date Added, newest first

    def test_queue_groups_only_list_real_batches(self) -> None:
        groups = dl.queue_groups(self.rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["label"], groups[0]["total"], groups[0]["active"]), ("Batch 1", 2, 2))

    def test_available_actions(self) -> None:
        by_key = {row["key"]: row for row in self.rows}
        self.assertEqual(dl.available_actions([]), set())
        running = dl.available_actions([by_key["q:run"]])
        self.assertIn("stop", running)
        self.assertIn("cancel", running)
        self.assertNotIn("resume", running)
        self.assertNotIn("open", running)
        failed = dl.available_actions([by_key["q:fail"]])
        self.assertTrue({"resume", "details", "redownload"} <= failed)
        done = dl.available_actions([by_key["h:zip"]])
        self.assertTrue({"open", "open_folder", "properties", "redownload"} <= done)
        multi = dl.available_actions([by_key["q:run"], by_key["q:fail"]])
        self.assertTrue({"stop", "resume", "delete"} <= multi)
        self.assertNotIn("properties", multi)

    def test_totals(self) -> None:
        rows = [dl.queue_row(snap(id="x", status="running", progress=0.5), {"speed_bps": 1024}),
                dl.queue_row(snap(id="y", status="running", progress=0.5), {"speed_bps": 1024})] + self.rows
        info = dl.totals(rows)
        self.assertEqual((info["downloading"], info["queued"], info["failed"]), (3, 1, 1))
        self.assertEqual(info["speed_text"], "2.00 KB/s")

    def test_columns_are_normalized(self) -> None:
        self.assertEqual(dl.normalize_columns(None), dl.DEFAULT_VISIBLE)
        self.assertEqual(dl.normalize_columns(["status", "bogus", "status", "name", "size"]), ["status", "size"])
        self.assertEqual(dl.normalize_columns([]), dl.DEFAULT_VISIBLE)


class StoreIntegrationTests(unittest.TestCase):
    def test_queue_snapshot_feeds_rows_and_remove(self) -> None:
        queue = DownloadQueue()
        request = queue.enqueue({"url": "https://a.com/v", "name": "Song", "download_dir": "D:/Music",
                                 "request_settings": {"mode": "Audio", "page_url": "https://a.com/page",
                                                      "history_quality": "MP3 192 kbps"}})
        row = dl.queue_row(queue.get(request.id))
        self.assertEqual((row["category"], row["save_to"], row["website"]), ("audio", "D:/Music", "a.com"))
        self.assertEqual(row["quality"], "MP3 192 kbps")
        self.assertFalse(queue.remove(request.id))  # still queued
        queue.request_cancel(request.id)
        self.assertTrue(queue.remove(request.id))
        self.assertIsNone(queue.get(request.id))

    def test_history_keeps_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoryStore(Path(tmp) / "h.json")
            entry = make_history_entry(None, status="failed", title="T", source_url="https://a.com/v",
                                       platform="a", started_at=time.time() - 5, finished_at=time.time(),
                                       request_id="abc123")
            store.add(entry)
            row = dl.history_row(store.list()[0])
        self.assertEqual((row["request_id"], row["status"]), ("abc123", "failed"))


if __name__ == "__main__":
    unittest.main()
