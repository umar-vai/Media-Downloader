"""Downloads keep working while the drive of a saved folder is not there."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
import native_forms  # noqa: E402
from download_rules import available_folder, missing_drive  # noqa: E402

OPTIONS = {"mode": "Video", "video_quality": "720p", "audio_format": "MP3", "audio_quality": "192"}


@unittest.skipUnless(os.name == "nt", "drive letters are a Windows thing")
class MissingDriveTests(unittest.TestCase):
    def setUp(self) -> None:
        # Q: is "unplugged" whatever this computer really has.
        real = os.path.isdir
        patcher = mock.patch("os.path.isdir",
                             lambda p: False if str(p).upper().startswith("Q:") else real(p))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.fallback = self.tmp / "Downloads"
        patcher = mock.patch.object(app_module, "DEFAULT_DOWNLOAD_DIR", self.fallback)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.queued: list[dict] = []
        self.statuses: list[str] = []

    def _fake(self, **settings) -> SimpleNamespace:
        def enqueue(payloads):
            self.queued.extend(payloads)
            return [SimpleNamespace(id=f"r{len(self.queued)}") for _ in payloads]

        return SimpleNamespace(
            settings=settings, download_dir=Path(r"Q:\video"),
            download_queue=SimpleNamespace(enqueue_batch=enqueue),
            _set_status=lambda text, *a: self.statuses.append(text),
            _schedule_download_activity_refresh=lambda *a: None,
            _start_available_downloads=lambda: None,
        )

    def _enqueue(self, fake, **kw) -> None:
        app_module.DownloaderApp._enqueue_batch_downloads(fake, "https://example.com/v", OPTIONS, **kw)

    def test_only_an_absent_drive_counts_as_missing(self) -> None:
        self.assertEqual(missing_drive(r"Q:\video\trans"), "Q:")
        self.assertEqual(missing_drive(self.tmp / "not-made-yet"), "")
        self.assertEqual(missing_drive("relative"), "")
        self.assertEqual(available_folder(self.tmp, self.fallback), (self.tmp, ""))
        folder, note = available_folder(Path(r"Q:\video"), self.fallback)
        self.assertEqual(folder, self.fallback)
        self.assertIn("Q:", note)

    def test_chosen_folder_works_while_the_default_drive_is_missing(self) -> None:
        self._enqueue(self._fake(), target_dir_override=self.tmp)
        self.assertEqual(self.queued[0]["download_dir"], str(self.tmp))

    def test_default_and_rule_folders_fall_back_and_say_so(self) -> None:
        for settings in ({}, {"folder_rules": r"example.com => Q:\rules"}):
            fake = self._fake(**settings)
            self._enqueue(fake)
            self.assertEqual(self.queued[-1]["download_dir"], str(self.fallback))
            self.assertIn("Q:", self.statuses[-1])
            self.assertEqual(fake.download_dir, Path(r"Q:\video"))

    def test_choosing_a_missing_drive_is_refused_clearly(self) -> None:
        with self.assertRaisesRegex(OSError, "Q: not found"):
            self._enqueue(self._fake(), target_dir_override=Path(r"Q:\video"))
        self.assertEqual(self.queued, [])

    def test_download_window_proposes_downloads_and_keeps_saved_folders(self) -> None:
        saved = {"site:example.com": r"Q:\video"}
        fake = self._fake(capture_dirs=dict(saved), capture_last_dir=r"Q:\video")
        fake.native_shell = SimpleNamespace(show_form=lambda form: None)
        fake._capture_dialogs = []
        fake._capture_ids = set()
        fake.pause_download = fake.resume_download = fake.cancel_download = fake.retry_download = None
        fake._enqueue_batch_downloads = lambda *a, **k: k["ids_out"].append("r1")
        made: dict = {}

        def form(shell, api, **kw):
            made.update(kw, api=api)
            made["form"] = SimpleNamespace(done=False)
            return made["form"]

        with mock.patch.object(native_forms, "CaptureForm", side_effect=form), \
                mock.patch.object(app_module, "save_settings"):
            app_module.DownloaderApp._open_capture_dialog(
                fake, "https://example.com/v", dict(OPTIONS), {}, direct=False, data={})
            self.assertEqual(made["folder"], str(self.fallback))
            self.assertIn("Q:", made["form"].status)
            made["api"].start("name", made["folder"], False, {"ext": False, "site": False})
        self.assertEqual(fake.settings["capture_dirs"], saved)
        self.assertEqual(fake.settings["capture_last_dir"], r"Q:\video")


if __name__ == "__main__":
    unittest.main()
