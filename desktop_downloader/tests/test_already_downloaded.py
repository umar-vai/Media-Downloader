from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module
import app_ui
import download_list as dl


class AlreadyDownloadedTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def row(self, name: str, url: str, **entry):
        path = self.folder / name
        path.write_bytes(b"data")
        return dl.history_row({"id": name, "file_path": str(path), "file_name": name, "source_url": url,
                               "download_url": url, "file_size": 4, **entry})

    def test_other_spellings_of_a_link_are_the_same_video(self) -> None:
        rows = [self.row("clip.mp4", "https://www.youtube.com/watch?v=abc123", mode="Video")]
        for url in ("https://youtu.be/abc123?si=xyz", "https://m.youtube.com/watch?v=abc123&feature=share",
                    "https://www.youtube.com/shorts/abc123"):
            self.assertIsNotNone(dl.find_downloaded(rows, url, mode="Video"), url)
        self.assertIsNone(dl.find_downloaded(rows, "https://youtu.be/other", mode="Video"))
        self.assertIsNone(dl.find_downloaded(rows, "https://youtu.be/abc123", mode="Audio"))

    def test_a_deleted_file_is_downloaded_without_asking(self) -> None:
        rows = [self.row("clip.mp4", "https://example.test/v/1")]
        (self.folder / "clip.mp4").unlink()
        self.assertIsNone(dl.find_downloaded(rows, "https://example.test/v/1"))

    def test_a_captured_stream_matches_its_page_but_not_a_feed(self) -> None:
        rows = [self.row("ep.mp4", "https://cdn.test/a.m3u8", page_url="https://site.test/watch/ep-4"),
                self.row("feed.mp4", "https://cdn.test/b.m3u8", page_url="https://site.test/")]
        self.assertIsNotNone(dl.find_downloaded(rows, "https://cdn.test/new.m3u8",
                                                page_url="https://site.test/watch/ep-4"))
        self.assertIsNone(dl.find_downloaded(rows, "https://cdn.test/c.m3u8", page_url="https://site.test/"))

    def test_files_match_on_link_or_name_and_size_not_on_the_page(self) -> None:
        rows = [self.row("setup.zip", "https://files.test/setup.zip", page_url="https://files.test/downloads")]
        self.assertIsNone(dl.find_downloaded(rows, "https://files.test/other.zip", direct=True,
                                             page_url="https://files.test/downloads"))
        self.assertIsNotNone(dl.find_downloaded(rows, "https://mirror.test/x?token=1", direct=True,
                                                file_name="setup.zip", size=4))
        self.assertIsNone(dl.find_downloaded(rows, "https://mirror.test/x", direct=True,
                                             file_name="setup.zip", size=5))

    def test_pasted_links_wait_for_the_answer(self) -> None:
        rows = [self.row("one.mp4", "https://example.test/v/1")]
        queued, asked = [], []
        fake = SimpleNamespace(
            _history_rows=rows,
            _enqueue_batch_downloads=lambda text, options, **kw: queued.append((text, kw)) or ("b", 1, 0),
            _ask_download_again=lambda matches, new, choose: asked.append((len(matches), new, choose)),
        )
        links = "https://example.test/v/1\nhttps://example.test/v/2"
        app_ui.UIMixin._queue_links(fake, links, {"mode": "Video"}, lambda *_a: None)
        self.assertEqual((queued, asked[0][:2]), ([], (1, 1)))
        asked[0][2]("new")
        self.assertEqual(queued, [("https://example.test/v/2", {})])
        asked[0][2]("all")
        self.assertEqual(queued[1], (links, {"extra": {"force_redownload": True}}))

    def test_a_browser_download_of_a_saved_file_asks_first(self) -> None:
        rows = [self.row("setup.zip", "https://files.test/setup.zip")]
        opened, asked = [], []
        fake = SimpleNamespace(
            _history_rows=rows, settings={"capture_dialog": True},
            _default_batch_options=lambda: {"mode": "Video", "video_quality": "720p"},
            _open_capture_dialog=lambda url, options, extra, **_kw: opened.append(extra),
            _ask_download_again=lambda matches, new, choose: asked.append(choose),
        )
        app_module.DownloaderApp._handle_external_download(
            fake, {"url": "https://files.test/setup.zip", "kind": "file", "fileName": "setup.zip"})
        self.assertEqual((opened, len(asked)), ([], 1))
        asked[0]("all")
        self.assertTrue(opened[0]["force_redownload"])


if __name__ == "__main__":
    unittest.main()
