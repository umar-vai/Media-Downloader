from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import download_list
import native_forms as nf
from native_shell import NativeShell, row_tag


def var(value):
    holder = Mock()
    holder.value = value
    holder.get.side_effect = lambda: holder.value
    holder.set.side_effect = lambda new: setattr(holder, "value", new)
    return holder


class NativeShellTests(unittest.TestCase):
    def make_shell(self, app):
        shell = NativeShell.__new__(NativeShell)
        shell.app = app
        shell.selected = []
        shell.selection_revision = 0
        shell.forms = {}
        shell.add_form = shell.batch_form = shell.updates_form = shell.options_form = None
        shell.diagnostics = None
        shell.closed = False
        shell.ready = False
        shell.publish = Mock()
        return shell

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
        self.assertEqual(shell._view["countText"], "1 item")
        self.assertIn("category", shell._view["rows"][0])

    def test_empty_view_explains_the_category(self):
        app = SimpleNamespace(_rows=[], _rows_by_key={}, _category="finished", _query="",
                              _sort_column="added", _sort_desc=True)
        shell = self.make_shell(app)
        shell._refresh_rows()
        self.assertEqual(shell._view["emptyTitle"], "No finished downloads")
        app._query = "cats"
        shell._refresh_rows()
        self.assertEqual(shell._view["emptyTitle"], "No matches")

    def test_row_tags_match_the_classic_list_colours(self):
        self.assertEqual(row_tag({"status": "failed"}), "failed")
        self.assertEqual(row_tag({"status": "missing"}), "missing")
        self.assertEqual(row_tag({"status": "paused"}), "dim")
        self.assertEqual(row_tag({"status": "complete"}), "")

    def test_form_events_reach_the_form(self):
        shell = self.make_shell(SimpleNamespace())
        form = Mock(done=False)
        shell.forms = {"f1": form}
        shell._dispatch({"command": "form", "value": {"form": "f1", "event": "click", "widget": "ok",
                                                      "values": {"a": 1}}})
        form.handle.assert_called_once_with("click", "ok", {"a": 1})

    def test_add_url_form_edits_and_downloads(self):
        app = SimpleNamespace(url_var=var(""), name_var=var(""), video_quality_var=var("720p"),
                              audio_format_var=var("MP3"), audio_quality_var=var("192"),
                              open_editor_after_var=var(False), _on_url_changed=Mock(), download=Mock(),
                              analyze_media=Mock(), _sync_mode=Mock())
        for name in ("video_quality", "audio_format", "audio_quality"):
            widget = Mock()
            widget.cget.return_value = ("720p", "1080p", "MP3", "M4A", "192", "256")
            setattr(app, name, widget)
        shell = self.make_shell(app)
        form = nf.AddUrlForm(shell)
        form.handle("change", "url", {"url": "https://example.test/v", "name": "Clip", "quality": "1080p",
                                      "edit": True})
        self.assertEqual(app.url_var.get(), "https://example.test/v")
        app._on_url_changed.assert_called_once_with()
        self.assertEqual(app.video_quality_var.get(), "1080p")
        self.assertTrue(app.open_editor_after_var.get())
        form.handle("enter", "url", {"url": "https://example.test/v"})
        app.analyze_media.assert_called_once_with()
        form.handle("click", "download", {"url": "https://example.test/v"})
        app.download.assert_called_once_with()

    def test_batch_form_queues_with_the_chosen_format(self):
        page = SimpleNamespace(mode_var=var("Video"), quality_var=var("720p"), audio_format_var=var("MP3"),
                               audio_quality_var=var("192"))
        app = SimpleNamespace(batch_page=page, _enqueue_batch_downloads=Mock(return_value=("batch", 2, 0)),
                              _after_batch_queued=Mock(), _set_status=Mock())
        shell = self.make_shell(app)
        form = nf.BatchForm(shell)
        links = "https://example.test/one\nhttps://example.test/two"
        form.handle("click", "queue", {"links": links, "audio": True, "format": "M4A", "bitrate": "256"})
        args = app._enqueue_batch_downloads.call_args[0]
        self.assertEqual(args[1], {"mode": "Audio", "video_quality": "720p", "audio_format": "M4A",
                                   "audio_quality": "256"})
        app._after_batch_queued.assert_called_once_with("batch")
        self.assertEqual(form.links, "")
        self.assertTrue(form.status.startswith("Queued 2 links"))

    def test_options_form_saves_settings_and_folder_rules(self):
        app = SimpleNamespace(settings={"theme": "Dark", "folder_rules": "example.test => D:/Videos | 720p"},
                              _apply_settings=Mock())
        shell = self.make_shell(app)
        shell.close_options = Mock()
        form = nf.OptionsForm(shell, "Save To")
        self.assertEqual(form.tab, 2)
        self.assertEqual(form.rules, [["example.test", "D:/Videos", "720p"]])
        form.handle("change", "fast_mode", {"fast_mode": False, "max_concurrent_downloads": "12"})
        self.assertTrue(form.dirty)
        form.handle("click", "ok", {})
        payload = app._apply_settings.call_args[0][0]
        self.assertFalse(payload["fast_mode"])
        self.assertEqual(payload["max_concurrent_downloads"], 10)
        self.assertEqual(payload["folder_rules"], "example.test => D:/Videos | 720p")
        shell.close_options.assert_called_once_with()

    def test_message_boxes_answer_like_tkinter(self):
        shell = self.make_shell(SimpleNamespace())
        with patch.object(nf, "run_modal", return_value="yes"):
            self.assertTrue(nf.message_box(shell, "askyesno", "T", "M"))
            self.assertEqual(nf.message_box(shell, "askquestion", "T", "M"), "yes")
        with patch.object(nf, "run_modal", return_value="cancel"):
            self.assertIsNone(nf.message_box(shell, "askyesnocancel", "T", "M"))
            self.assertFalse(nf.message_box(shell, "askokcancel", "T", "M"))
        with patch.object(nf, "run_modal", return_value="ok"):
            self.assertEqual(nf.message_box(shell, "showerror", "T", "M"), "ok")

    def test_confirm_delete_returns_the_files_choice(self):
        shell = self.make_shell(SimpleNamespace())
        form = nf.ConfirmDeleteForm(shell, 2, 1, False)
        shell.remove_form = Mock()
        form.handle("click", "delete", {"files": True})
        self.assertEqual(form.result, (True, True))

    def test_capture_form_starts_and_follows_progress(self):
        api = Mock()
        api.start.return_value = "r1"
        api.state.return_value = ({"status": "running", "progress": 0.5, "name": "File.zip", "detail": "Downloading"},
                                  {"total_bytes": 200, "downloaded_bytes": 100})
        app = SimpleNamespace(after=Mock())
        shell = self.make_shell(app)
        form = nf.CaptureForm(shell, api, name="File", folder=str(Path.cwd()), source="example.test", is_file=True)
        form.orig_ext = ".zip"
        form.handle("click", "download", {"name": "File", "folder": str(Path.cwd())})
        self.assertEqual(api.start.call_args[0][0], "File.zip")
        self.assertEqual(form.mode, "progress")
        self.assertEqual(form.window_title(), "50% - File.zip")
        self.assertIn("100 B of 200 B", form.stats)


if __name__ == "__main__":
    unittest.main()
