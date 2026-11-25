"""Transport between the existing desktop controller and its Qt window."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from typing import Any

from batch_download import MAX_BATCH_URLS, parse_batch_urls_detailed
import download_list as dl
from download_rules import parse_rules
from main_window import TREE_LAYOUT
from options_dialog import rules_from_rows
from version import APP_VERSION

LOG = logging.getLogger(__name__)
DIALOG_ACTIONS = {
    "add": "open_add_url",
    "batch": "open_batch_downloads",
    "settings": "open_settings",
    "editor": "open_editor",
    "screen": "open_screen_recorder",
    "audio": "open_audio_recorder",
    "diagnostics": "open_diagnostics",
    "install_extension": "export_browser_extension",
    "palette": "open_command_palette",
    "choose_folder": "choose_download_folder",
    "reset_folder": "reset_download_folder",
    "clean_parts": "clean_partial_files",
    "delete_completed": "delete_completed",
}
SIMPLE_ACTIONS = {
    "open_downloads": "open_download_folder",
    "open_log": "open_app_log",
    "refresh": "refresh_list",
    "pause_all": "pause_all_downloads",
    "resume_all": "resume_all_downloads",
    "retry_all": "retry_all_failed",
    "remove_missing": "remove_missing",
    "quit": "quit_from_tray",
    "close": "_close_app",
    "focus": "_on_focus_in",
}
ROW_ACTIONS = {
    "open": "open_selected",
    "open_folder": "open_selected_folder",
    "edit": "edit_selected",
    "resume": "resume_selected",
    "stop": "stop_selected",
    "cancel": "cancel_selected",
    "redownload": "redownload_selected",
    "copy_link": "copy_selected_links",
    "copy_file": "copy_selected_files",
    "delete": "delete_selected",
    "properties": "properties_selected",
    "details": "details_selected",
    "default": "_open_default",
}


SETTINGS_FIELDS = (
    "download_dir", "default_mode", "video_quality", "audio_format", "audio_quality",
    "max_concurrent_downloads", "auto_analyze_links", "open_editor_after_download", "confirm_before_exit",
    "close_to_tray", "capture_dialog", "start_with_windows", "auto_check_updates", "auto_download_updates",
    "theme", "skip_existing", "organize_by_site", "fast_mode", "embed_metadata", "embed_thumbnail",
    "subtitles", "subtitle_langs", "clipboard_hotkey", "extension_enabled", "cookies_file", "cookies_browser",
)


def executable() -> Path | None:
    override = os.environ.get("MEDIA_DOWNLOADER_NATIVE_UI")
    if override and not getattr(sys, "frozen", False):
        path = Path(override).resolve()
    elif os.name == "nt" and getattr(sys, "frozen", False) and "--classic-ui" not in sys.argv:
        path = Path(sys._MEIPASS) / "native_desktop" / "MediaDownloaderDesktop.exe"
    else:
        return None
    return path if path.is_file() else None


class NativeShell:
    def __init__(self, app: Any, path: Path) -> None:
        self.app = app
        args = [str(path)]
        icon = app._icon_file
        if icon:
            args.extend(("--icon", icon))
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=path.parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.status = ""
        self.add_visible = False
        self.batch_visible = False
        self.batch_links = ""
        self.batch_status = ""
        self.batch_count = f"0 / {MAX_BATCH_URLS} links"
        self.update_visible = False
        self.settings_visible = False
        self.settings_revision = 0
        self.settings_tab = "General"
        self.settings_status = ""
        self.add_status = ""
        self.add_thumbnail = ""
        self.rows_changed = True
        self._view: dict[str, Any] = {}
        self.visible = not app._start_minimized
        self.ready = False
        self.closed = False
        self.started = time.monotonic()
        self.selected: list[str] = []
        self.selection_revision = 0
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._changed = threading.Condition()
        self._pending: dict[str, Any] | None = None
        self._last_state: dict[str, Any] | None = None
        self._visibility_revision = 0
        threading.Thread(target=self._read, daemon=True, name="qt-commands").start()
        threading.Thread(target=self._write, daemon=True, name="qt-state").start()
        app.after(30, self._poll)

    def _read(self) -> None:
        with self.process.stdout as pipe:
            for line in pipe:
                try:
                    command = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(command, dict):
                    while not self.closed:
                        try:
                            self._messages.put(command, timeout=0.2)
                        except queue.Full:
                            continue
                        break

    def _write(self) -> None:
        pipe = self.process.stdin
        try:
            while True:
                with self._changed:
                    self._changed.wait_for(lambda: self._pending is not None or self.closed)
                    if self.closed:
                        break
                    state, self._pending = self._pending, None
                pipe.write(json.dumps(state, separators=(",", ":")).encode("ascii") + b"\n")
                pipe.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            pipe.close()

    def close(self) -> None:
        with self._changed:
            self.closed = True
            self._changed.notify()
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def _poll(self) -> None:
        if self.closed:
            return
        app = self.app
        if self.process.poll() is not None or (not self.ready and time.monotonic() - self.started > 12):
            LOG.warning("Native desktop unavailable (exit code %s), reopening desktop window", self.process.poll())
            self.close()
            app.native_shell = None
            app._query = ""
            app._show_category("all")
            app.show_from_tray()
            app._render_download_activity()
            return
        for _ in range(30):
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(message)
            except (tk.TclError, ValueError, OSError):
                if self.closed:
                    return
                LOG.exception("Native desktop command failed")
            if self.closed:
                return
        if self.ready and self.visible:
            self.publish()
        app.after(100, self._poll)

    def show(self, visible: bool) -> None:
        self.visible = visible
        self._visibility_revision += 1
        self.publish()

    def select(self, keys: list[str]) -> None:
        self.selected = keys
        self.selection_revision += 1

    def _dispatch(self, message: dict[str, Any]) -> None:
        app = self.app
        command = message.get("command")
        if not isinstance(command, str):
            return
        if command == "ready":
            self.ready = True
        elif command == "category":
            key = message.get("value")
            if key in [category["key"] for category in self.categories()]:
                self.select([])
                app._select_category(key)
        elif command == "search":
            value = message.get("value")
            if isinstance(value, str):
                app._search_changed(value[:2000])
        elif command == "sort":
            if message.get("value") in dl.COLUMN_KEYS:
                app._sort_by(message["value"])
        elif command == "updates":
            app.show_page("updates")
            if not app.top_update_button.available:
                app.check_for_updates(manual=False, inline=True)
        elif command == "update_check":
            app.check_for_updates(manual=False, inline=True)
        elif command == "update_install" and not app.update_action_button.instate(["disabled"]):
            app.download_or_install_update()
        elif command == "update_cancel":
            app.cancel_update_download()
        elif command == "update_later":
            app.snooze_update()
            self.update_visible = False
        elif command == "update_release":
            app.release_button.invoke()
        elif command == "update_close":
            self.update_visible = False
        elif command == "settings_save":
            self._save_settings(message.get("value"))
        elif command == "settings_close":
            self.settings_visible = False
        elif command == "settings_check_updates":
            if not self.settings_visible:
                app.show_page("updates")
                app.check_for_updates(manual=False, inline=True)
        elif command == "copy_pairing_token":
            app._copy_text(str(app.settings.get("extension_token") or ""), "pairing token")
        elif command == "browser_settings":
            app.open_settings(tab="Browser")
        elif command == "column" and isinstance(message.get("value"), str) and message["value"] in app._column_vars:
            var = app._column_vars[message["value"]]
            var.set(not var.get())
            app._apply_columns()
        elif command == "reset_columns":
            app._reset_columns()
        elif command == "width":
            value = message.get("value")
            if isinstance(value, dict) and value.get("column") in dl.COLUMN_KEYS:
                width = value.get("width")
                if isinstance(width, int) and 70 <= width <= 4096:
                    app.settings["list_widths"] = {**app.settings.get("list_widths", {}), value["column"]: width}
                    app._persist_settings()
        elif command in {"navigation", "details_pane"}:
            setting, var = ("show_categories", app._show_categories_var) if command == "navigation" else (
                "show_details", app._show_details_var)
            var.set(not var.get())
            app.settings[setting] = var.get()
            app._persist_settings()
        elif command == "theme" and message.get("value") in ("Light", "Dark"):
            app._theme_var.set(message["value"])
            app._choose_theme()
        elif command in {"add_url", "add_analyze", "add_stop", "add_close", "add_submit"}:
            self._add_command(command, message.get("value"))
        elif command in {"batch_links", "batch_submit", "batch_close"}:
            self._batch_command(command, message.get("value"))
        elif command == "clipboard":
            mode = "Audio" if message.get("value") == "Audio" else "Video"
            app._download_clipboard_link(mode)
        elif command in DIALOG_ACTIONS:
            getattr(app, DIALOG_ACTIONS[command])()
        elif command in SIMPLE_ACTIONS:
            getattr(app, SIMPLE_ACTIONS[command])()
        elif command in ROW_ACTIONS or command == "selection":
            keys = message.get("keys")
            if isinstance(keys, list):
                self.selected = [key for key in keys if isinstance(key, str) and key in app._rows_by_key]
            actions = dl.available_actions(app._selected_rows())
            if command in ROW_ACTIONS and (command in actions or command == "default"):
                getattr(app, ROW_ACTIONS[command])()

    def _add_command(self, command: str, value: Any) -> None:
        app = self.app
        if not self.add_visible:
            return
        if command == "add_close":
            self.add_visible = False
            return
        if command == "add_stop":
            app.cancel_analysis()
            return
        if command == "add_submit" and isinstance(value, dict):
            url = str(value.get("url") or "").strip()
            if url == app.url_var.get().strip() == app.last_analyzed_url and app.current_info is not None:
                app.name_var.set(str(value.get("name") or "")[:350])
                app._sync_mode("Audio" if value.get("mode") == "Audio" else "Video")
                choices = (
                    ("quality", app.video_quality, app.video_quality_var),
                    ("format", app.audio_format, app.audio_format_var),
                    ("bitrate", app.audio_quality, app.audio_quality_var),
                )
                for key, widget, variable in choices:
                    choice = value.get(key)
                    if choice in widget.cget("values"):
                        variable.set(choice)
                app.open_editor_after_var.set(value.get("edit") is True)
                app.download()
            return
        if command in {"add_url", "add_analyze"} and isinstance(value, str):
            if value != app.url_var.get():
                app.url_var.set(value[:8192])
                app._on_url_changed()
            if command == "add_analyze":
                app.analyze_media()

    def _add_form(self) -> dict[str, Any]:
        app = self.app
        if not self.add_visible:
            return {"visible": False}
        info = app.current_info or {}
        ready = bool(info) and app.url_var.get().strip() == app.last_analyzed_url
        return {
            "visible": True,
            "url": app.url_var.get(),
            "mode": app.mode_var.get(),
            "quality": app.video_quality_var.get(),
            "format": app.audio_format_var.get(),
            "bitrate": app.audio_quality_var.get(),
            "edit": app.open_editor_after_var.get(),
            "folder": str(app.download_dir),
            "name": app.name_var.get(),
            "title": str(app.title_label.cget("text")),
            "metadata": str(app.meta_label.cget("text")),
            "running": app.analysis_running,
            "ready": ready,
            "direct": bool(info.get("direct_file")),
            "status": self.add_status,
            "thumbnail": self.add_thumbnail,
        }

    def _batch_command(self, command: str, value: Any) -> None:
        app = self.app
        if not self.batch_visible:
            return
        if command == "batch_close":
            if isinstance(value, str):
                self._batch_command("batch_links", value)
            self.batch_visible = False
        elif command == "batch_links" and isinstance(value, str):
            self.batch_links = value[:2_000_000]
            self.batch_status = ""
            try:
                urls, duplicates, invalid = parse_batch_urls_detailed(self.batch_links)
            except ValueError as exc:
                self.batch_count = f"0 / {MAX_BATCH_URLS} links"
                if self.batch_links.strip():
                    self.batch_status = str(exc)
            else:
                self.batch_count = f"{len(urls)} / {MAX_BATCH_URLS} links"
                if duplicates or invalid:
                    self.batch_status = f"{duplicates} duplicate · {invalid} invalid"
        elif command == "batch_submit" and isinstance(value, dict):
            links = str(value.get("links") or "")[:2_000_000]
            options = {
                "mode": "Audio" if value.get("mode") == "Audio" else "Video",
                "video_quality": str(value.get("quality") or "720p"),
                "audio_format": str(value.get("format") or "MP3"),
                "audio_quality": str(value.get("bitrate") or "192"),
            }
            try:
                batch_id, queued, duplicates = app._enqueue_batch_downloads(links, options)
            except (ValueError, OSError) as exc:
                self.batch_status = str(exc)
            else:
                self.batch_links = ""
                self.batch_status = ""
                self.batch_count = f"0 / {MAX_BATCH_URLS} links"
                app._after_batch_queued(batch_id)
                skipped = f" · {duplicates} duplicate skipped" if duplicates else ""
                app._set_status(f"Queued {queued} batch downloads{skipped}", "ready")

    def _batch_dialog(self) -> dict[str, Any]:
        page = self.app.batch_page
        if not self.batch_visible:
            return {"visible": False}
        return {
            "visible": True,
            "links": self.batch_links,
            "status": self.batch_status,
            "count": self.batch_count,
            "mode": page.mode_var.get(),
            "quality": page.quality_var.get(),
            "format": page.audio_format_var.get(),
            "bitrate": page.audio_quality_var.get(),
        }

    def _update_dialog(self) -> dict[str, Any]:
        app = self.app
        if not self.update_visible:
            return {"visible": False}
        return {
            "visible": True,
            "headline": app.update_status_label.cget("text"),
            "detail": app.update_detail_label.cget("text"),
            "notes": app.update_notes_label.cget("text"),
            "progress": int(float(app.update_progress.cget("value"))),
            "downloading": app.update_downloading,
            "canCheck": not app.update_check_button.instate(["disabled"]),
            "canInstall": not app.update_action_button.instate(["disabled"]),
            "canSnooze": not app.update_later_button.instate(["disabled"]),
            "canCancel": not app.update_cancel_button.instate(["disabled"]),
            "releaseText": app.release_button.cget("text"),
            "installText": app.update_action_button.cget("text"),
        }

    def open_settings(self, tab: str | None) -> None:
        self.settings_tab = {"Website folders": "Save To", "About": "Updates"}.get(tab, tab) or "General"
        self.settings_visible = True
        self.settings_revision += 1
        self.settings_status = ""

    def _save_settings(self, value: Any) -> None:
        app = self.app
        if not isinstance(value, dict) or not self.settings_visible or not isinstance(value.get("values"), dict):
            return
        payload = {key: data for key, data in value["values"].items() if key in SETTINGS_FIELDS}
        rules = value.get("rules")
        if isinstance(rules, list):
            rows = [tuple(row) for row in rules if isinstance(row, list) and len(row) == 3]
            payload["folder_rules"] = rules_from_rows(rows)
        try:
            app._apply_settings(payload)
        except (ValueError, OSError, RuntimeError) as exc:
            self.settings_status = str(exc)
        else:
            self.settings_status = "Settings saved."
            self.settings_tab = str(value.get("tab") or self.settings_tab)
            self.settings_visible = value.get("close") is not True
            self.settings_revision += 1

    def _settings_dialog(self) -> dict[str, Any]:
        if not self.settings_visible:
            return {"visible": False}
        app = self.app
        return {
            "visible": True,
            "revision": self.settings_revision,
            "tab": self.settings_tab,
            "status": self.settings_status,
            "extension": str(app.statusbar.extension.cget("text")),
            "values": {key: app.settings[key] for key in SETTINGS_FIELDS if key in app.settings},
            "rules": [[rule.host, rule.folder, rule.quality]
                      for rule in parse_rules(str(app.settings.get("folder_rules") or ""))],
        }

    def categories(self) -> list[dict[str, str]]:
        counts = dl.category_counts(self.app._rows)
        fixed = [{"key": key, "name": label, "count": str(counts[key])}
                 for key, _parent, label, _icon in TREE_LAYOUT if key != "queues"]
        added = [{"key": group["key"], "name": group["label"], "count": f"{group['done']}/{group['total']}"}
                 for group in dl.queue_groups(self.app._rows)]
        return fixed + added

    def _refresh_rows(self) -> None:
        app = self.app
        categories = self.categories()
        category = next((item for item in categories if item["key"] == app._category), None)
        if category is None:
            app._category = "queue:active"
            category = next(item for item in categories if item["key"] == app._category)
        shown = dl.sort_rows(dl.filter_rows(app._rows, app._category, app._query), app._sort_column, app._sort_desc)
        app._shown_keys = [row["key"] for row in shown]

        shown_keys = set(app._shown_keys)
        lost = {key[2:] for key in self.selected if key.startswith("q:") and key not in app._rows_by_key}
        selection = [key for key in self.selected if key in shown_keys]
        selection.extend(row["key"] for row in shown if row["request_id"] in lost)
        if selection != self.selected:
            self.select(selection)

        fields = ("key", "name", "size_text", "status", "status_text", "progress", "speed_text", "eta_text",
                  "last_try_text", "added_text", "website", "quality", "save_to")
        self._view = {
            "categories": categories,
            "category": category,
            "rows": [
                {**{field: row[field] for field in fields},
                 "dragPath": row["path"] if row["status"] == "complete" and row["file_exists"] else "",
                 "actions": sorted(dl.available_actions([row]))}
                for row in shown
            ],
            "totals": dl.totals(app._rows),
        }
        self.rows_changed = False

    def publish(self) -> None:
        app = self.app
        if self.rows_changed:
            self._refresh_rows()
        selected = app._selected_rows()
        state = {
            **self._view,
            "version": APP_VERSION,
            "updateAvailable": app.top_update_button.available,
            "addForm": self._add_form(),
            "batchDialog": self._batch_dialog(),
            "updateDialog": self._update_dialog(),
            "settingsDialog": self._settings_dialog(),
            "visible": self.visible,
            "visibilityRevision": self._visibility_revision,
            "selectionRevision": self.selection_revision,
            "selection": self.selected,
            "summary": dl.selection_summary(selected),
            "sort": app._sort_column,
            "descending": app._sort_desc,
            "status": self.status,
            "updates": str(app.top_update_button.note.cget("text")),
            "columns": ["name"] + dl.normalize_columns(app.settings.get("list_columns")),
            "details": bool(app.settings.get("show_details", True)),
            "navigation": bool(app.settings.get("show_categories", True)),
            "theme": app.settings.get("theme", "Light"),
            "widths": app.settings.get("list_widths", {}),
            "selectedDetails": selected[0]["detail"] if len(selected) == 1 else "",
            "extension": str(app.statusbar.extension.cget("text")),
        }
        if state != self._last_state:
            self._last_state = state
            with self._changed:
                self._pending = state
                self._changed.notify()
