from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from app import DownloaderApp
from single_instance import cleanup_stale_updater_copies


class TraySafeUpdateTests(unittest.TestCase):
    def fake_app(self):
        queue = Mock()
        queue.queued_count.return_value = 0
        queue.running_count.return_value = 0
        queue.snapshot.return_value = []
        tray = Mock()
        return SimpleNamespace(
            _quitting=False,
            _update_handoff_started=False,
            tray=tray,
            settings={"close_to_tray": True, "confirm_before_exit": True},
            download_queue=queue,
            analysis_running=False,
            update_downloading=False,
            analysis_cancel_event=None,
            update_cancel_event=None,
            download_cancel_events={},
            hide_to_tray=Mock(),
            destroy=Mock(),
            show_from_tray=Mock(),
            _close_app=Mock(),
            latest_release=SimpleNamespace(version="3.11.3"),
            downloaded_update=Path("downloaded-update.exe"),
            update_action_button=Mock(),
            update_detail_label=Mock(),
            after=Mock(),
        )

    @patch("app.power.keep_system_awake")
    def test_normal_close_still_hides_to_tray(self, _power):
        app = self.fake_app()
        DownloaderApp._close_app(app)
        app.hide_to_tray.assert_called_once_with()
        app.tray.stop.assert_not_called()
        app.destroy.assert_not_called()

    @patch("app.power.keep_system_awake")
    def test_update_close_stops_tray_and_destroys_window(self, _power):
        app = self.fake_app()
        DownloaderApp._close_app(app, for_update=True)
        app.tray.stop.assert_called_once_with()
        app.hide_to_tray.assert_not_called()
        app.destroy.assert_called_once_with()
        self.assertTrue(app._quitting)

    @patch("app.power.keep_system_awake")
    @patch("app.messagebox.askyesno", return_value=False)
    def test_cancelled_tray_quit_preserves_close_to_tray(self, ask, _power):
        app = self.fake_app()
        app._quitting = True
        app.download_queue.running_count.return_value = 1
        DownloaderApp._close_app(app)
        ask.assert_called_once()
        self.assertFalse(app._quitting)
        app.tray.stop.assert_not_called()
        app.destroy.assert_not_called()

    @patch("app.append_update_log")
    @patch("app.is_frozen_windows_app", return_value=True)
    @patch("app.is_installed_mode", return_value=False)
    @patch("app.subprocess.Popen")
    @patch("app.get_bundled_updater_path")
    def test_update_handoff_starts_once_and_requests_full_close(
        self, bundled, popen, _installed, _frozen, _log,
    ):
        app = self.fake_app()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exe = folder / "MediaDownloaderUpdater.exe"
            exe.write_bytes(b"dummy-updater")
            bundled.return_value = exe
            with patch("app.UPDATE_DIR", folder):
                DownloaderApp._launch_updater(app)
                DownloaderApp._launch_updater(app)
            self.assertTrue(app._update_handoff_started)
            popen.assert_called_once()
            self.assertIn("MediaDownloaderUpdater-run-", popen.call_args.args[0][0])
            app.after.assert_called_once()
            delay, close_callback = app.after.call_args.args
            self.assertEqual(delay, 250)
            close_callback()
            app._close_app.assert_called_once_with(for_update=True)

    @patch("app.append_update_log")
    @patch("app.is_frozen_windows_app", return_value=True)
    @patch("app.messagebox.askyesno", return_value=False)
    @patch("app.get_bundled_updater_path")
    @patch("app.subprocess.Popen")
    def test_busy_app_requires_confirmation_before_update(
        self, popen, bundled, ask, _frozen, _log,
    ):
        app = self.fake_app()
        app.download_queue.queued_count.return_value = 2
        DownloaderApp._launch_updater(app)
        ask.assert_called_once()
        bundled.assert_not_called()
        popen.assert_not_called()
        self.assertFalse(app._update_handoff_started)

    def test_cleanup_removes_only_old_updater_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            old = folder / "MediaDownloaderUpdater-run-old.exe"
            recent = folder / "MediaDownloaderUpdater-run-new.exe"
            unrelated = folder / "MediaDownloader.exe"
            for item in (old, recent, unrelated):
                item.write_bytes(b"mock")
            past = time.time() - 3600
            os.utime(old, (past, past))
            cleanup_stale_updater_copies(folder, min_age_seconds=600)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
