"""Transport between the desktop controller and its native (C++) window.

The controller (``app.DownloaderApp``) keeps running exactly as before, with its Tk window
hidden. This class streams what the window shows to ``MediaDownloaderDesktop.exe`` as one JSON
object per line, and turns the window's commands back into controller calls.
"""

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

import download_list as dl
import native_forms as nf
from main_window import TREE_LAYOUT, details_view, toolbar_states, totals_summary
from version import APP_VERSION

LOG = logging.getLogger(__name__)
POLL_MS = 30

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
    "cleanup": "cleanup_list",
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
    "hide": "_hide_window",
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
EMPTY_TEXT = {
    "all": ("No downloads yet", "Click Add URL (Ctrl+N) and paste a link, or send a video from your browser\n"
                                "with the Media Downloader extension. Every download shows up here."),
    "unfinished": ("Nothing unfinished", "Downloads that are running, queued, paused or failed appear here."),
    "finished": ("No finished downloads", "Completed downloads are listed here with their size and folder."),
    "queue:active": ("The queue is empty", "Running, waiting and paused downloads appear here."),
}


def executable() -> Path | None:
    override = os.environ.get("MEDIA_DOWNLOADER_NATIVE_UI")
    if override and not getattr(sys, "frozen", False):
        path = Path(override).resolve()
    elif os.name == "nt" and getattr(sys, "frozen", False) and "--classic-ui" not in sys.argv:
        path = Path(sys._MEIPASS) / "native_desktop" / "MediaDownloaderDesktop.exe"
    else:
        return None
    return path if path.is_file() else None


def row_tag(row: dict[str, Any]) -> str:
    status = row["status"]
    if status == "failed":
        return "failed"
    if status == "missing":
        return "missing"
    if status in {"paused", "cancelled", "queued"}:
        return "dim"
    return ""


class NativeShell:
    def __init__(self, app: Any, path: Path) -> None:
        self.app = app
        args = [str(path), "--theme", "Dark" if str(app.settings.get("theme") or "Light") == "Dark" else "Light"]
        icon = app._icon_file
        if icon:
            args.extend(("--icon", icon))
        if getattr(sys, "frozen", False):
            # What a taskbar pin of the window starts: the app itself, not the native window.
            args.extend(("--launcher", sys.executable))
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=path.parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.status = ""
        self.add_thumbnail = ""
        self.rows_changed = True
        self._view: dict[str, Any] = {}
        self.visible = not app._start_minimized
        self.ready = False
        self.closed = False
        self.started = time.monotonic()
        self.selected: list[str] = []
        self.selection_revision = 0
        self.forms: dict[str, nf.NativeForm] = {}
        self.add_form: nf.AddUrlForm | None = None
        self.batch_form: nf.BatchForm | None = None
        self.updates_form: nf.UpdatesForm | None = None
        self.options_form: nf.OptionsForm | None = None
        self.diagnostics: nf.DiagnosticsForm | None = None
        self._file_request: dict[str, Any] | None = None
        self._file_reply: dict[str, Any] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        self._changed = threading.Condition()
        self._pending: dict[str, Any] | None = None
        self._last_state: str | None = None
        self._visibility_revision = 0
        self._restore_hooks = nf.install_dialog_hooks(self)
        threading.Thread(target=self._read, daemon=True, name="native-commands").start()
        threading.Thread(target=self._write, daemon=True, name="native-state").start()
        app.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ compatibility
    # app.py / app_ui.py use these flags to open and close the dialogs.
    @property
    def add_visible(self) -> bool:
        return self.add_form is not None

    @add_visible.setter
    def add_visible(self, on: bool) -> None:
        if on and self.add_form is None:
            self.add_form = nf.AddUrlForm(self)
            self.add_form.focus = "url"
            self.show_form(self.add_form)
        elif not on and self.add_form is not None:
            self.close_add()

    @property
    def add_status(self) -> str:
        return ""

    @add_status.setter
    def add_status(self, _text: str) -> None:
        pass  # the Add URL form reads the status label itself

    @property
    def batch_visible(self) -> bool:
        return self.batch_form is not None

    @batch_visible.setter
    def batch_visible(self, on: bool) -> None:
        if on and self.batch_form is None:
            self.batch_form = nf.BatchForm(self)
            self.show_form(self.batch_form)
        elif not on and self.batch_form is not None:
            self.close_batch()

    @property
    def update_visible(self) -> bool:
        return self.updates_form is not None

    @update_visible.setter
    def update_visible(self, on: bool) -> None:
        if on and self.updates_form is None:
            self.updates_form = nf.UpdatesForm(self)
            self.show_form(self.updates_form)
        elif not on and self.updates_form is not None:
            self.close_updates()

    def close_add(self) -> None:
        form, self.add_form = self.add_form, None
        if form is not None:
            form.close()

    def close_batch(self) -> None:
        form, self.batch_form = self.batch_form, None
        if form is not None:
            form.close()

    def close_updates(self) -> None:
        form, self.updates_form = self.updates_form, None
        if form is not None:
            form.close()

    def close_options(self) -> None:
        form, self.options_form = self.options_form, None
        if form is not None:
            form.close()

    def open_settings(self, tab: str | None) -> None:
        if self.options_form is None:
            self.options_form = nf.OptionsForm(self, tab)
            self.show_form(self.options_form)
        else:
            self.options_form.select_tab(tab)
            self.options_form.flash = not self.options_form.flash

    # ------------------------------------------------------------------------- forms
    def show_form(self, form: nf.NativeForm) -> None:
        self.forms[form.form_id] = form
        self.publish(force=True)

    def remove_form(self, form: nf.NativeForm) -> None:
        self.forms.pop(form.form_id, None)
        if form is self.add_form:
            self.add_form = None
        if form is self.batch_form:
            self.batch_form = None
        if form is self.updates_form:
            self.updates_form = None
        if form is self.options_form:
            self.options_form = None
        self.publish(force=True)

    def minimize_form(self, form: nf.NativeForm) -> None:
        form.minimize_rev = getattr(form, "minimize_rev", 0) + 1

    def form_specs(self) -> list[dict[str, Any]]:
        specs = []
        for form in list(self.forms.values()):
            try:
                spec = form.spec()
            except Exception:  # noqa: BLE001
                LOG.exception("Native dialog %s could not be described", form.form_id)
                continue
            spec["minimizeRev"] = getattr(form, "minimize_rev", 0)
            specs.append(spec)
        return specs

    def file_dialog(self, kind: str, options: dict[str, Any]) -> list[str]:
        """Shows a Windows file picker in the native window and waits for the answer."""
        filters = []
        for item in options.get("filetypes") or []:
            try:
                name, pattern = item
            except (TypeError, ValueError):
                continue
            if isinstance(pattern, (list, tuple)):
                pattern = " ".join(pattern)
            filters.append([str(name), str(pattern)])
        request = {
            "id": f"f{time.monotonic_ns()}", "kind": kind, "title": str(options.get("title") or ""),
            "filters": filters, "initialDir": str(options.get("initialdir") or ""),
            "fileName": str(options.get("initialfile") or ""), "defaultExt": str(options.get("defaultextension") or ""),
        }
        top = next((f for f in reversed(list(self.forms.values()))), None)
        if top is not None:
            request["owner"] = top.form_id
        self._file_reply = None
        self._file_request = request
        var = tk.BooleanVar(self.app, value=False)
        self._file_var = var
        self.publish(force=True)
        self.app.wait_variable(var)
        reply, self._file_reply = self._file_reply or {}, None
        self._file_request = None
        return [str(p) for p in reply.get("paths") or [] if p]

    # ------------------------------------------------------------------------ transport
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
                pipe.write(state.encode("ascii") + b"\n")
                pipe.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def close(self) -> None:
        with self._changed:
            self.closed = True
            self._changed.notify()
        self._restore_hooks()
        for form in list(self.forms.values()):
            if form._done_var is not None:
                try:
                    form._done_var.set(True)
                except tk.TclError:
                    pass
        file_var = getattr(self, "_file_var", None)
        if file_var is not None:
            try:
                file_var.set(True)
            except tk.TclError:
                pass
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def _poll(self) -> None:
        if self.closed:
            return
        app = self.app
        # Scheduled first: a command below may open a modal dialog, which waits in a nested loop
        # that still needs this poll to receive the dialog's answer.
        app.after(POLL_MS, self._poll)
        if self.process.poll() is not None or (not self.ready and time.monotonic() - self.started > 12):
            LOG.warning("Native desktop unavailable (exit code %s), reopening desktop window", self.process.poll())
            self.close()
            app.native_shell = None
            app._query = ""
            app._show_category("all")
            app.show_from_tray()
            app._render_download_activity()
            return
        for _ in range(60):
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(message)
            except (tk.TclError, ValueError, OSError, KeyError, TypeError):
                if self.closed:
                    return
                LOG.exception("Native desktop command failed")
            if self.closed:
                return
        if self.ready:
            self.publish()

    def show(self, visible: bool) -> None:
        self.visible = visible
        self._visibility_revision += 1
        self.publish(force=True)

    def select(self, keys: list[str]) -> None:
        self.selected = keys
        self.selection_revision += 1

    # ------------------------------------------------------------------------- commands
    def _dispatch(self, message: dict[str, Any]) -> None:
        app = self.app
        command = message.get("command")
        if not isinstance(command, str):
            return
        value = message.get("value")
        if command == "ready":
            self.ready = True
            self.rows_changed = True
        elif command == "form":
            if isinstance(value, dict):
                form = self.forms.get(str(value.get("form") or ""))
                if form is not None and not form.done:
                    values = value.get("values") if isinstance(value.get("values"), dict) else {}
                    form.handle(str(value.get("event") or ""), str(value.get("widget") or ""), values)
                    self.publish(force=True)
        elif command == "file_dialog":
            request = self._file_request
            if isinstance(value, dict) and request is not None and value.get("id") == request["id"]:
                self._file_reply = value
                var = getattr(self, "_file_var", None)
                if var is not None:
                    var.set(True)
        elif command == "category":
            if value in [category["key"] for category in self.categories()]:
                self.select([])
                app._select_category(value)
        elif command == "search":
            if isinstance(value, str):
                app._search_changed(value[:2000])
        elif command == "sort":
            if value in dl.COLUMN_KEYS:
                app._sort_by(value)
        elif command == "updates":
            app.show_page("updates")
            if not app.top_update_button.available:
                app.check_for_updates(manual=False, inline=True)
        elif command == "update_badge":
            app.top_update_button._clicked()
        elif command == "check_updates":
            app.show_page("updates")
            app.check_for_updates(manual=False, inline=True)
        elif command == "copy_pairing_token":
            app._copy_text(str(app.settings.get("extension_token") or ""), "pairing token")
        elif command == "browser_settings":
            app.open_settings(tab="Browser")
        elif command == "column" and isinstance(value, str) and value in app._column_vars:
            var = app._column_vars[value]
            var.set(not var.get())
            app._apply_columns()
            self.rows_changed = True
        elif command == "reset_columns":
            app._reset_columns()
            self.rows_changed = True
        elif command == "width":
            if isinstance(value, dict) and value.get("column") in dl.COLUMN_KEYS:
                width = value.get("width")
                if isinstance(width, (int, float)) and 48 <= width <= 4096:
                    app.settings["list_widths"] = {**app.settings.get("list_widths", {}), value["column"]: int(width)}
                    app._persist_settings()
        elif command == "sidebar_width":
            if isinstance(value, (int, float)) and 120 <= value <= 2000:
                app.settings["sidebar_width"] = int(value)
                app._persist_settings()
        elif command in {"navigation", "details_pane"}:
            setting, var = ("show_categories", app._show_categories_var) if command == "navigation" else (
                "show_details", app._show_details_var)
            var.set(not var.get())
            app.settings[setting] = var.get()
            app._persist_settings()
        elif command == "theme" and value in ("Light", "Dark"):
            app._theme_var.set(value)
            app._choose_theme()
        elif command == "clipboard":
            app._download_clipboard_link("Audio" if value == "Audio" else "Video")
        elif command in DIALOG_ACTIONS:
            getattr(app, DIALOG_ACTIONS[command])()
        elif command in SIMPLE_ACTIONS:
            getattr(app, SIMPLE_ACTIONS[command])()
        elif command in ROW_ACTIONS or command in {"selection", "copy_page", "open_page"}:
            keys = message.get("keys")
            if isinstance(keys, list):
                self.selected = [key for key in keys if isinstance(key, str) and key in app._rows_by_key]
            rows = app._selected_rows()
            actions = dl.available_actions(rows)
            if command in {"copy_page", "open_page"}:
                if rows and "open_page" in actions:
                    page = str(rows[0].get("page_url") or "")
                    if command == "copy_page":
                        app._copy_text(page, "web page link")
                    else:
                        import webbrowser

                        webbrowser.open(page)
            elif command in ROW_ACTIONS and (command in actions or command == "default"):
                getattr(app, ROW_ACTIONS[command])()

    # ---------------------------------------------------------------------------- state
    def categories(self) -> list[dict[str, str]]:
        counts = dl.category_counts(self.app._rows)
        fixed = [{"key": key, "name": label, "count": str(counts.get(key, 0))}
                 for key, _parent, label, _icon in TREE_LAYOUT if key != "queues"]
        added = [{"key": group["key"], "name": group["label"], "count": f"{group['done']}/{group['total']}"}
                 for group in dl.queue_groups(self.app._rows)]
        return fixed + added

    def _refresh_rows(self) -> None:
        app = self.app
        categories = self.categories()
        category = next((item for item in categories if item["key"] == app._category), None)
        if category is None:
            app._category = "all"
            category = next(item for item in categories if item["key"] == "all")
        shown = dl.sort_rows(dl.filter_rows(app._rows, app._category, app._query), app._sort_column, app._sort_desc)
        app._shown_keys = [row["key"] for row in shown]

        shown_keys = set(app._shown_keys)
        lost = {key[2:] for key in self.selected if key.startswith("q:") and key not in app._rows_by_key}
        selection = [key for key in self.selected if key in shown_keys]
        selection.extend(row["key"] for row in shown if row["request_id"] in lost)
        if selection != self.selected:
            self.select(selection)

        fields = ("key", "name", "size_text", "status", "status_text", "progress", "speed_text", "eta_text",
                  "last_try_text", "added_text", "website", "quality", "save_to", "category")
        total = len(shown)
        if total:
            empty = ("", "")
        elif app._query:
            empty = ("No matches", f"No downloads match “{app._query}”.")
        else:
            empty = EMPTY_TEXT.get(app._category) or ("Nothing here yet", "Downloads of this type appear here.")
        self._view = {
            "categories": categories,
            "category": category,
            "countText": f"{total} item{'s' if total != 1 else ''}" if total else "",
            "emptyTitle": empty[0],
            "emptyText": empty[1],
            "rows": [
                {**{field: row[field] for field in fields},
                 "tag": row_tag(row),
                 "dragPath": row["path"] if row["status"] == "complete" and row["file_exists"] else "",
                 "actions": sorted(dl.available_actions([row]))}
                for row in shown
            ],
            "totals": dl.totals(app._rows),
        }
        self.rows_changed = False

    def _update_badge(self) -> dict[str, Any]:
        badge = self.app.top_update_button
        note = badge.note
        color = str(note.cget("fg")).upper()
        from theme import C

        kind = {C["SUCCESS"].upper(): "success", C["DANGER"].upper(): "danger", C["MUTED"].upper(): "muted"}.get(color, "faint")
        return {
            "note": str(note.cget("text")) if note.winfo_manager() or badge.available else str(note.cget("text")),
            "kind": kind,
            "bold": badge.available,
            "button": str(badge.button.cget("text")),
            "enabled": badge.button.enabled,
        }

    def _extension_menu(self) -> str:
        server = getattr(self.app, "extension_server", None)
        if server is None:
            return "Browser link is starting…"
        if server.extension_active():
            return f"Extension connected (port {server.port})"
        return f"Listening on port {server.port}; waiting for the extension"

    def publish(self, force: bool = False) -> None:
        if self.closed or (not self.ready and not force):
            return
        app = self.app
        if self.rows_changed and self.visible:
            self._refresh_rows()
        selected = app._selected_rows()
        totals = self._view.get("totals") or dl.totals(app._rows)
        state = {
            **self._view,
            "version": APP_VERSION,
            "visible": self.visible,
            "visibilityRevision": self._visibility_revision,
            "selectionRevision": self.selection_revision,
            "selection": self.selected,
            "sort": app._sort_column,
            "descending": app._sort_desc,
            "status": self.status,
            "summaryText": totals_summary(totals),
            "speedText": f"Speed: {totals['speed_text']}" if totals.get("speed_text") else "",
            "toolbar": toolbar_states(app._rows, selected, totals),
            "detailsPane": details_view(selected[0] if len(selected) == 1 else None, len(selected),
                                        dl.selection_summary(selected)),
            "actionCounts": dl.action_counts(selected) if len(selected) > 1 else {},
            "updateBadge": self._update_badge(),
            "columns": ["name"] + dl.normalize_columns(app.settings.get("list_columns")),
            "details": bool(app.settings.get("show_details", True)),
            "navigation": bool(app.settings.get("show_categories", True)),
            "theme": app.settings.get("theme", "Light"),
            "widths": app.settings.get("list_widths", {}),
            "sidebarWidth": int(app.settings.get("sidebar_width") or 214),
            "extension": str(app.statusbar.extension.cget("text")),
            "extensionMenu": self._extension_menu(),
            "forms": self.form_specs(),
            "fileDialog": self._file_request,
        }
        line = json.dumps(state, separators=(",", ":"), default=str)
        if line != self._last_state or force:
            self._last_state = line
            with self._changed:
                self._pending = line
                self._changed.notify()
