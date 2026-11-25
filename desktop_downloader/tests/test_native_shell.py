from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import download_list
from native_shell import NativeShell


class NativeShellTests(unittest.TestCase):
    def make_shell(self, app):
        shell = NativeShell.__new__(NativeShell)
        shell.app = app
        shell.add_visible = True
        shell.selected = []
        shell.selection_revision = 0
        return shell

    def test_submit_only_queues_the_url_that_was_analyzed(self):
        app = SimpleNamespace(url_var=Mock(), last_analyzed_url="https://example.test/new",
                              current_info={"title": "Media"}, download=Mock())
        app.url_var.get.return_value = app.last_analyzed_url
        shell = self.make_shell(app)

        shell._add_command("add_submit", {"url": "https://example.test/old"})
        app.download.assert_not_called()

        app.name_var = Mock()
        app.video_quality = Mock()
        app.video_quality.cget.return_value = ("720p", "1080p")
        app.video_quality_var = Mock()
        app.audio_format = Mock()
        app.audio_format.cget.return_value = ("MP3", "M4A")
        app.audio_format_var = Mock()
        app.audio_quality = Mock()
        app.audio_quality.cget.return_value = ("192", "256")
        app.audio_quality_var = Mock()
        app.open_editor_after_var = Mock()
        app._sync_mode = Mock()
        shell._add_command("add_submit", {
            "url": app.last_analyzed_url, "name": "Sample", "mode": "Audio",
            "quality": "720p", "format": "M4A", "bitrate": "192", "edit": True,
        })
        app.download.assert_called_once_with()
        app.name_var.set.assert_called_once_with("Sample")
        app.audio_format_var.set.assert_called_once_with("M4A")
        app._sync_mode.assert_called_once_with("Audio")
        app.open_editor_after_var.set.assert_called_once_with(True)

    def test_selection_moves_to_matching_history_entry(self):
        row = download_list.history_row({
            "id": "history", "request_id": "queue", "file_path": "Sample.mp4",
        }, exists=True)
        app = SimpleNamespace(_rows=[row], _rows_by_key={row["key"]: row}, _category="all",
                              _query="", _sort_column="added", _sort_desc=True)
        shell = self.make_shell(app)
        shell.selected = ["q:queue"]
        shell._refresh_rows()
        self.assertEqual(shell.selected, ["h:history"])
        self.assertEqual(shell.selection_revision, 1)
        self.assertEqual(shell._view["rows"][0]["dragPath"], "Sample.mp4")

    def test_install_command_uses_existing_update_action_when_enabled(self):
        app = SimpleNamespace(update_action_button=Mock(), download_or_install_update=Mock())
        shell = self.make_shell(app)
        app.update_action_button.instate.return_value = True
        shell._dispatch({"command": "update_install"})
        app.download_or_install_update.assert_not_called()
        app.update_action_button.instate.return_value = False
        shell._dispatch({"command": "update_install"})
        app.download_or_install_update.assert_called_once_with()
