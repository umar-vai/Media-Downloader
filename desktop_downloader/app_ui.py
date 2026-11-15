"""Main window of Media Downloader, laid out like a classic Windows download manager.

* menu bar (Tasks, File, Downloads, View, Help) and a toolbar of large icon buttons;
* a category tree on the left (All Downloads by file type, Unfinished, Finished, Queues);
* one dense, sortable, multi-select table of every download - live ones from the queue and
  finished ones from the history - with a right-click menu, a details pane and a status bar;
* Add URL, Batch and Updates/About are dialogs; Options opens the settings dialog.

The table's data comes from ``download_list`` (pure functions, unit tested); the widgets
live in ``main_window``.
"""

from __future__ import annotations

import io
import os
import subprocess
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageTk

import download_list as dl
import theme
import ui_kit as ui
from batch_page import BatchSection
from history_window import PropertiesDialog
from icons import IconSet
from main_window import (CategoryTree, ConfirmDelete, DetailsPane, DownloadTable, PageDialog, SearchBox, StatusBar,
                         Toolbar, UpdateBadge, setup_style)
from theme import C
from ui_kit import px
from version import APP_VERSION

THUMB_W, THUMB_H = 184, 104
_ICON_FILE = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
STATUS_STYLES = {
    "ready": (ui.SUCCESS_SOFT, ui.SUCCESS, "READY"),
    "working": (ui.INFO_SOFT, ui.CYAN, "WORKING"),
    "success": (ui.SUCCESS_SOFT, ui.SUCCESS, "COMPLETE"),
    "cancelled": (ui.WARNING_SOFT, ui.WARNING, "CANCELLED"),
    "error": (ui.DANGER_SOFT, ui.DANGER, "FAILED"),
}
EMPTY_TEXT = {
    "all": ("No downloads yet", "Click Add URL (Ctrl+N) and paste a link, or send a video from your browser\n"
                                "with the Media Downloader extension. Every download shows up here."),
    "unfinished": ("Nothing unfinished", "Downloads that are running, queued, paused or failed appear here."),
    "finished": ("No finished downloads", "Completed downloads are listed here with their size and folder."),
    "queue:active": ("The queue is empty", "Running, waiting and paused downloads appear here."),
}


def reveal_in_folder(path: str) -> None:
    """Open Explorer with the file selected (or the folder itself when there is no file)."""
    target = Path(os.path.normpath(path))
    try:
        if os.name == "nt":
            if target.is_file():
                # One pre-quoted string: a list argument makes explorer ignore /select.
                subprocess.Popen(f'explorer /select,"{target}"')
            elif target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif target.parent.exists():
                os.startfile(str(target.parent))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
    except Exception:
        pass


class UIMixin:
    """Builds and refreshes the visible UI. Mixed into ``DownloaderApp``."""

    # ================================================================== build
    def _build_ui(self) -> None:
        ui.init(self)
        ui.install_wheel(self)
        setup_style(self)
        self.current_page = "list"
        self.icons = IconSet(self, ink=C["MUTED"], ring=C["SURFACE"])
        self._icon_file = str(_ICON_FILE) if _ICON_FILE.exists() else None

        sort = self.settings.get("list_sort")
        column, descending = (sort if isinstance(sort, list) and len(sort) == 2 else ["added", True])
        self._sort_column = column if column in dl.COLUMN_KEYS else "added"
        self._sort_desc = bool(descending)
        self._category = "all"
        self._query = ""
        self._rows: list[dict[str, Any]] = []
        self._rows_by_key: dict[str, dict[str, Any]] = {}
        self._history_rows: list[dict[str, Any]] | None = None
        self._history_entries: dict[str, dict[str, Any]] = {}
        self._history_built_at = 0.0
        self._discard_ids: set[str] = set()
        self._heading_drag = False
        self._shown_keys: list[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_menubar()
        self._build_toolbar()
        tk.Frame(self, bg=C["BORDER_SOFT"], height=1).grid(row=1, column=0, sticky="ew")
        self._build_body()
        self.statusbar = StatusBar(self)
        self.statusbar.grid(row=3, column=0, sticky="ew")
        self.statusbar.version.configure(text=f"v{APP_VERSION}")

        self._build_add_dialog()
        self._build_batch_dialog()
        self._build_about_dialog()
        self._bind_shortcuts()

        self._sync_mode(self.mode_var.get())
        self.category_tree.select("all")
        self.table.mark_sort(self._sort_column, self._sort_desc)
        self._render_download_activity()
        self.after(250, self.table.focus_table)

    # ---------------------------------------------------------------- menus
    def _build_menubar(self) -> None:
        bar = tk.Menu(self, tearoff=0)

        tasks = tk.Menu(bar, tearoff=0)
        tasks.add_command(label="Add URL…", accelerator="Ctrl+N", command=self.open_add_url)
        tasks.add_command(label="Add batch download…", accelerator="Ctrl+B", command=self.open_batch_downloads)
        tasks.add_separator()
        tasks.add_command(label="Download link from clipboard", accelerator="Ctrl+Alt+D",
                          command=lambda: self._download_clipboard_link("Video"))
        tasks.add_command(label="Download audio from clipboard", accelerator="Ctrl+Alt+M",
                          command=lambda: self._download_clipboard_link("Audio"))
        tasks.add_separator()
        tasks.add_command(label="Command palette…", accelerator="Ctrl+K", command=self.open_command_palette)
        tasks.add_separator()
        tasks.add_command(label="Hide window", command=self._hide_window)
        tasks.add_command(label="Exit", command=self.quit_from_tray)
        bar.add_cascade(label="Tasks", menu=tasks, underline=0)

        file = tk.Menu(bar, tearoff=0)
        file.add_command(label="Open", accelerator="Enter", command=self.open_selected)
        file.add_command(label="Open folder", command=self.open_selected_folder)
        file.add_command(label="Open in media editor", command=self.edit_selected)
        file.add_separator()
        file.add_command(label="Open download folder", command=self.open_download_folder)
        file.add_command(label="Choose download folder…", command=self.choose_download_folder)
        file.add_separator()
        file.add_command(label="Media editor…", command=lambda: self.open_editor())
        bar.add_cascade(label="File", menu=file, underline=0)
        self._file_menu = file

        downloads = tk.Menu(bar, tearoff=0, postcommand=self._refresh_menu_states)
        downloads.add_command(label="Resume", command=self.resume_selected)
        downloads.add_command(label="Stop", command=self.stop_selected)
        downloads.add_command(label="Cancel download", command=self.cancel_selected)
        downloads.add_command(label="Download again", command=self.redownload_selected)
        downloads.add_separator()
        downloads.add_command(label="Start all", command=self.resume_all_downloads)
        downloads.add_command(label="Stop all", command=self.pause_all_downloads)
        downloads.add_command(label="Retry all failed", command=self.retry_all_failed)
        downloads.add_separator()
        downloads.add_command(label="Delete…", accelerator="Del", command=self.delete_selected)
        downloads.add_command(label="Delete completed…", command=self.delete_completed)
        downloads.add_command(label="Remove missing files from list", command=self.remove_missing)
        downloads.add_separator()
        downloads.add_command(label="Copy download link", accelerator="Ctrl+C", command=self.copy_selected_links)
        downloads.add_command(label="Error details…", command=self.details_selected)
        downloads.add_command(label="Properties", accelerator="Alt+Enter", command=self.properties_selected)
        downloads.add_separator()
        downloads.add_command(label="Select all", accelerator="Ctrl+A", command=self.select_all)
        bar.add_cascade(label="Downloads", menu=downloads, underline=0)
        self._downloads_menu = downloads
        file.configure(postcommand=self._refresh_menu_states)

        view = tk.Menu(bar, tearoff=0)
        self._show_categories_var = tk.BooleanVar(value=bool(self.settings.get("show_categories", True)))
        self._show_details_var = tk.BooleanVar(value=bool(self.settings.get("show_details", True)))
        view.add_checkbutton(label="Categories", variable=self._show_categories_var, command=self._apply_panes)
        view.add_checkbutton(label="Download details", variable=self._show_details_var, command=self._apply_panes)
        columns = tk.Menu(view, tearoff=0)
        visible = dl.normalize_columns(self.settings.get("list_columns"))
        self._column_vars: dict[str, tk.BooleanVar] = {}
        for key, title, _width, _anchor in dl.COLUMNS:
            if key == "name":
                continue
            var = tk.BooleanVar(value=key in visible)
            self._column_vars[key] = var
            columns.add_checkbutton(label=title, variable=var, command=self._apply_columns)
        columns.add_separator()
        columns.add_command(label="Reset columns", command=self._reset_columns)
        view.add_cascade(label="Columns", menu=columns)
        view.add_separator()
        themes = tk.Menu(view, tearoff=0)
        self._theme_var = tk.StringVar(value=theme.normalize_theme(self.settings.get("theme") or theme.NAME))
        for name in theme.THEMES:
            themes.add_radiobutton(label=name, value=name, variable=self._theme_var, command=self._choose_theme)
        view.add_cascade(label="Theme", menu=themes)
        view.add_separator()
        view.add_command(label="Refresh", accelerator="F5", command=self.refresh_list)
        bar.add_cascade(label="View", menu=view, underline=0)

        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Check for updates…", command=lambda: (self.show_page("updates"),
                                                                           self.check_for_updates(manual=True)))
        help_menu.add_command(label="Updates & about", command=lambda: self.show_page("updates"))
        help_menu.add_separator()
        help_menu.add_command(label="Install browser extension…", command=self.export_browser_extension)
        help_menu.add_command(label="Diagnostics…", command=self.open_diagnostics)
        help_menu.add_command(label="Open app log", command=self.open_app_log)
        bar.add_cascade(label="Help", menu=help_menu, underline=0)
        tk.Tk.config(self, menu=bar)  # type: ignore[arg-type]

    def _refresh_menu_states(self) -> None:
        actions = dl.available_actions(self._selected_rows())
        totals = dl.totals(self._rows)
        states = {
            "Resume": "resume", "Stop": "stop", "Cancel download": "cancel", "Download again": "redownload",
            "Delete…": "delete", "Copy download link": "copy_link", "Error details…": "details",
            "Properties": "properties",
        }
        for label, key in states.items():
            self._downloads_menu.entryconfigure(label, state="normal" if key in actions else "disabled")
        self._downloads_menu.entryconfigure("Start all", state="normal" if totals["paused"] else "disabled")
        self._downloads_menu.entryconfigure(
            "Stop all", state="normal" if totals["downloading"] + totals["queued"] else "disabled")
        self._downloads_menu.entryconfigure(
            "Retry all failed", state="normal" if totals["failed"] else "disabled")
        for label, key in (("Open", "open"), ("Open folder", "open_folder"), ("Open in media editor", "edit")):
            self._file_menu.entryconfigure(label, state="normal" if key in actions else "disabled")

    # -------------------------------------------------------------- toolbar
    def _build_toolbar(self) -> None:
        bar = Toolbar(self, self.icons)
        bar.grid(row=0, column=0, sticky="ew")
        bar.add("add", "Add URL", "add", self.open_add_url, "Add a download link  (Ctrl+N)")
        bar.add("batch", "Batch", "batch", self.open_batch_downloads, "Add many links at once  (Ctrl+B)")
        bar.separator()
        bar.add("resume", "Resume", "resume", self.resume_selected, "Resume the selected downloads")
        bar.add("stop", "Stop", "stop", self.stop_selected, "Stop the selected downloads (they can be resumed)")
        bar.add("start_all", "Start All", "queue_start", self.resume_all_downloads, "Resume every stopped download")
        bar.add("stop_all", "Stop All", "stop_all", self.pause_all_downloads, "Stop every running and queued download")
        bar.separator()
        bar.add("delete", "Delete", "delete", self.delete_selected, "Remove the selected downloads  (Del)")
        bar.add("delete_done", "Delete Completed", "delete_done", self.delete_completed,
                "Remove finished downloads from the list")
        bar.separator()
        bar.add("options", "Options", "settings", self.open_settings, "Settings")
        bar.add("browsers", "Browsers", "browser", self._browser_menu, "Browser extension")
        bar.add("editor", "Editor", "editor", lambda: self.open_editor(), "Cut, convert and export media files")
        self.toolbar = bar
        self.top_update_button = UpdateBadge(bar.right, self.icons, lambda: self.show_page("updates"))

    # ----------------------------------------------------------------- body
    def _build_body(self) -> None:
        self.body = tk.PanedWindow(self, orient="horizontal", sashwidth=px(5), bg=C["BORDER_SOFT"], bd=0,
                                   sashrelief="flat", showhandle=False, opaqueresize=True)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.category_tree = CategoryTree(self.body, self.icons, self._select_category)
        right = tk.Frame(self.body, bg=C["SURFACE"])
        self._right = right
        self.body.add(self.category_tree, minsize=px(160), width=px(214), stretch="never")
        self.body.add(right, minsize=px(460), stretch="always")

        header = tk.Frame(right, bg=C["SURFACE"])
        header.pack(side="top", fill="x")
        self.list_title = tk.Label(header, text="All Downloads", bg=C["SURFACE"], fg=C["TEXT"],
                                   font=ui.font(16, True), anchor="w")
        self.list_title.pack(side="left", padx=(px(14), px(8)), pady=(px(9), px(8)))
        self.list_count = tk.Label(header, text="", bg=C["SURFACE"], fg=C["MUTED"], font=ui.font(13), anchor="w")
        self.list_count.pack(side="left", pady=(px(11), px(8)))
        self.search = SearchBox(header, self._search_changed)
        self.search.pack(side="right", padx=px(12), pady=px(7))
        tk.Frame(right, bg=C["BORDER_SOFT"], height=1).pack(side="top", fill="x")

        self.details = DetailsPane(right, self.icons, {
            "toggle": self._toggle_selected, "cancel": self.cancel_selected, "open": self.open_selected,
            "open_folder": self.open_selected_folder, "more": self._details_or_properties,
        })
        self.details.pack(side="bottom", fill="x")
        self.table = DownloadTable(right, self.icons, on_select=self._update_selection_ui, on_open=self._open_default,
                                   on_context=self._show_context_menu, on_sort=self._sort_by,
                                   on_delete=self.delete_selected)
        self.table.pack(side="top", fill="both", expand=True)
        self.table.set_widths(self.settings.get("list_widths") or {})
        self.table.tree.bind("<ButtonPress-1>", self._heading_press, add="+")
        self.table.tree.bind("<ButtonRelease-1>", self._heading_release, add="+")
        self.table.tree.bind("<Alt-Return>", lambda _e: self.properties_selected())
        self.table.tree.bind("<Control-c>", lambda _e: self.copy_selected_links())
        self._apply_columns(save=False)
        self._apply_panes(save=False)

    # ----------------------------------------------------------- shortcuts
    def _bind_shortcuts(self) -> None:
        for seq in ("<Control-n>", "<Control-N>"):
            self.bind_all(seq, lambda _e: self.open_add_url())
        for seq in ("<Control-b>", "<Control-B>"):
            self.bind_all(seq, lambda _e: self.open_batch_downloads())
        for seq in ("<Control-f>", "<Control-F>"):
            self.bind(seq, lambda _e: self.search.focus())
        self.bind("<F5>", lambda _e: self.refresh_list())
        self.bind("<Map>", lambda _e: self._schedule_download_activity_refresh(60), add="+")
        self.bind("<FocusIn>", self._on_focus_in, add="+")

    def _on_focus_in(self, _event: Any = None) -> None:
        # Files can be moved or deleted outside the app; check again when the user comes back.
        if self._history_rows is not None and time.monotonic() - self._history_built_at > 20:
            self._invalidate_history()

    # ============================================================== dialogs
    def _build_add_dialog(self) -> None:
        dialog = PageDialog(self, "Add URL", self._layout_add, size=(860, 720), minsize=(720, 560),
                            icon=self._icon_file)
        self.add_dialog = dialog
        p = dialog.page
        self.add_title = ui.Label(p, "New download", fg=ui.TEXT, size=21, bold=True)
        self.add_sub = ui.Label(p, "Paste a link to a video or audio page. It is analyzed automatically.",
                                fg=ui.MUTED, size=12)
        self.status_chip = ui.Chip(p, "READY", fg=ui.SUCCESS, bg=ui.SUCCESS_SOFT)
        self.status_label = ui.Label(p, "", fg=ui.MUTED, size=11, anchor="ne", ellipsis=True, relayout=False)

        self.link_card = ui.Card(p)
        self.link_cap = ui.Label(p, "ADDRESS", fg=ui.CYAN, size=10, bold=True)
        self.url_entry = ui.Field(p, var=self.url_var, size=13,
                                  placeholder="https://  — paste any public media link")
        self.url_entry.bind("<Return>", lambda _e: self.analyze_media())
        self.url_entry.bind("<KeyRelease>", self._on_url_changed)
        self.paste_button = ui.Btn(p, "Paste", self.paste_from_clipboard, style="secondary", size=12)
        self.read_button = ui.Btn(p, "Analyze", self.analyze_media, style="primary", size=12)
        self.cancel_analysis_button = ui.Btn(p, "Cancel", self.cancel_analysis, style="ghost", size=12,
                                             state="disabled")

        self.media_card = ui.Card(p)
        self.thumb = ui.Thumb(p)
        self.media_badge = ui.Chip(p, "WAITING FOR LINK", fg=ui.MUTED, bg=ui.SURFACE_2, size=9)
        self.title_label = ui.Label(p, "Analyze a link to see its details", fg=ui.TEXT, size=16, bold=True, wrap=True)
        self.meta_label = ui.Label(p, "Title, creator, platform and duration appear here.", fg=ui.MUTED, size=12,
                                   wrap=True)
        self.media_action_hint = ui.Label(p, "Paste a link — it analyzes automatically.", fg=ui.FAINT, size=11,
                                          wrap=True)

        self.opts_card = ui.Card(p)
        self.opts_cap = ui.Label(p, "SAVE AS", fg=ui.CYAN, size=10, bold=True)
        self.cap_dl_type = ui.Label(p, "DOWNLOAD TYPE", fg=ui.MUTED, size=9, bold=True)
        self.mode_control = ui.Segmented(p, ["Video", "Audio"], self.mode_var, command=self._sync_mode)
        self.cap_name = ui.Label(p, "FILE NAME", fg=ui.MUTED, size=9, bold=True)
        self.name_entry = ui.Field(p, var=self.name_var, placeholder="Filled in from the title")
        self.cap_folder = ui.Label(p, "SAVE TO", fg=ui.MUTED, size=9, bold=True)
        self.folder_entry = ui.Field(p, var=self.download_dir_var, readonly=True, fg=ui.MUTED)
        self.choose_folder_button = ui.Btn(p, "Browse", self.choose_download_folder, style="secondary", size=11)
        self.reset_folder_button = ui.Btn(p, "Default", self.reset_download_folder, style="ghost", size=11)
        self.quality_label = ui.Label(p, "VIDEO QUALITY", fg=ui.MUTED, size=9, bold=True)
        self.video_quality = ui.Select(p, ["360p", "480p", "720p", "1080p", "Best available"], self.video_quality_var)
        self.audio_format_label = ui.Label(p, "AUDIO FORMAT", fg=ui.MUTED, size=9, bold=True)
        self.audio_format = ui.Select(p, ["MP3", "M4A", "FLAC", "OPUS", "WAV"], self.audio_format_var)
        self.audio_quality_label = ui.Label(p, "BITRATE (KBPS)", fg=ui.MUTED, size=9, bold=True)
        self.audio_quality = ui.Select(p, ["128", "192", "256", "320"], self.audio_quality_var)
        self.output_hint = ui.Label(p, "MP4 video • FFmpeg merge when needed", fg=ui.FAINT, size=10)
        self.open_editor_after_check = ui.Check(p, "Open in editor after download", self.open_editor_after_var)
        self.add_close_button = ui.Btn(p, "Close", dialog.close, style="ghost", size=12)
        self.download_button = ui.Btn(p, "Download video", self.download, style="cyan", size=13, state="disabled")
        self.status_label.show(False)

    def _layout_add(self, w: int, h: int) -> int:
        x0 = px(24)
        cw = w - 2 * x0 - px(4)
        pad = px(18)
        y = px(18)
        self.add_title.place(x0, y)
        self.add_sub.place(x0, y + px(34))
        chip_w = self.status_chip.natural_w()
        self.status_chip.place(x0 + cw - chip_w, y + px(6))
        if self.status_label.visible:
            self.status_label.place(x0 + cw, y + px(36), max(60, min(px(360), cw - px(420))))
        y += px(66)

        card_h = px(100)
        self.link_card.place(x0, y, cw, card_h)
        self.link_cap.place(x0 + pad, y + px(16))
        bx = x0 + cw - pad
        for button in (self.cancel_analysis_button, self.read_button, self.paste_button):
            bw = button.natural_w(minimum=88 if button is not self.paste_button else 80)
            button.place(bx - bw, y + px(40), bw, px(42))
            bx -= bw + px(8)
        self.url_entry.place(x0 + pad, y + px(40), bx - (x0 + pad) + px(4), px(42))
        y += card_h + px(12)

        tx = x0 + pad + px(THUMB_W) + pad
        tw = x0 + cw - pad - tx
        self.thumb.place(x0 + pad, y + pad, px(THUMB_W), px(THUMB_H))
        self.media_badge.place(tx, y + pad)
        ty = y + px(48)
        h1 = self.title_label.place(tx, ty, tw)
        h2 = self.meta_label.place(tx, ty + h1 + px(6), tw)
        h3 = self.media_action_hint.place(tx, ty + h1 + px(6) + h2 + px(8), tw)
        text_h = (ty - y) + h1 + px(6) + h2 + px(8) + h3 + pad
        media_h = max(px(THUMB_H) + 2 * pad, text_h)
        self.media_card.place(x0, y, cw, media_h)
        y += media_h + px(12)

        top = y
        col_gap = px(24)
        col_w = (cw - 2 * pad - col_gap) / 2
        lx, rx = x0 + pad, x0 + pad + col_w + col_gap
        self.opts_cap.place(lx, top + px(16))
        r0 = top + px(44)
        field_h = px(40)
        self.cap_dl_type.place(lx, r0)
        self.mode_control.place(lx, r0 + px(20), col_w, field_h)
        self.cap_name.place(lx, r0 + px(70))
        self.name_entry.place(lx, r0 + px(90), col_w, field_h)
        self.cap_folder.place(lx, r0 + px(140))
        browse_w = self.choose_folder_button.natural_w(minimum=84)
        default_w = self.reset_folder_button.natural_w(minimum=76)
        self.reset_folder_button.place(lx + col_w - default_w, r0 + px(160), default_w, field_h)
        self.choose_folder_button.place(lx + col_w - default_w - px(8) - browse_w, r0 + px(160), browse_w, field_h)
        self.folder_entry.place(lx, r0 + px(160), col_w - default_w - browse_w - px(16), field_h)

        self.quality_label.place(rx, r0)
        self.video_quality.place(rx, r0 + px(20), col_w, field_h)
        half = (col_w - px(12)) / 2
        self.audio_format_label.place(rx, r0 + px(70))
        self.audio_quality_label.place(rx + half + px(12), r0 + px(70))
        self.audio_format.place(rx, r0 + px(90), half, field_h)
        self.audio_quality.place(rx + half + px(12), r0 + px(90), half, field_h)
        self.output_hint.place(rx, r0 + px(146))
        opts_h = (r0 + px(160) + field_h + pad) - top
        self.opts_card.place(x0, top, cw, opts_h)
        y = top + opts_h + px(14)

        self.open_editor_after_check.place(x0 + px(2), y, px(44))
        dl_w = max(px(190), self.download_button.natural_w())
        self.download_button.place(x0 + cw - dl_w, y, dl_w, px(44))
        close_w = self.add_close_button.natural_w(minimum=92)
        self.add_close_button.place(x0 + cw - dl_w - px(10) - close_w, y, close_w, px(44))
        return int(y + px(44) + px(18))

    def _build_batch_dialog(self) -> None:
        dialog = PageDialog(self, "Add batch download", self._layout_batch, size=(860, 600), minsize=(700, 500),
                            icon=self._icon_file)
        self.batch_dialog = dialog
        p = dialog.page
        self.batch_title = ui.Label(p, "Batch download", fg=ui.TEXT, size=21, bold=True)
        self.batch_sub = ui.Label(p, "Paste up to 300 links, one per line. They download in the background and "
                                     "show up in the list under Queues.", fg=ui.MUTED, size=12)
        self.batch_page = BatchSection(p, on_enqueue=self._enqueue_batch_downloads, settings=self.settings,
                                       on_queued=self._after_batch_queued, on_close=dialog.close)

    def _layout_batch(self, w: int, h: int) -> int:
        x0 = px(24)
        cw = w - 2 * x0 - px(4)
        y = px(20)
        self.batch_title.place(x0, y)
        self.batch_sub.place(x0, y + px(34))
        end = self.batch_page.layout(x0, y + px(72), cw, h)
        return int(end + px(22))

    def _build_about_dialog(self) -> None:
        dialog = PageDialog(self, "Updates & about", self._layout_updates, size=(760, 540), minsize=(620, 440),
                            icon=self._icon_file)
        self.about_dialog = dialog
        p = dialog.page
        self.up_title = ui.Label(p, "Media Downloader", fg=ui.TEXT, size=21, bold=True)
        self.up_sub = ui.Label(p, f"Version {APP_VERSION} • Updates & about", fg=ui.MUTED, size=12)
        self.update_card_bg = ui.Card(p)
        self.update_status_label = ui.Label(p, f"Current version: v{APP_VERSION}", fg=ui.TEXT, size=15, bold=True)
        self.update_check_button = ui.Btn(p, "Check for updates", lambda: self.check_for_updates(manual=True),
                                          style="secondary", size=11)
        self.update_detail_label = ui.Label(
            p,
            "Automatic update checks are enabled." if self.auto_check_updates_var.get()
            else "Automatic update checks are disabled.",
            fg=ui.MUTED, size=11, wrap=True)
        self.update_notes_label = ui.Label(p, "", fg=ui.MUTED, size=11, wrap=True)
        self.update_progress = ui.Bar(p, height=8, color=ui.ACCENT)
        self.update_action_button = ui.Btn(p, "Update now", self.download_or_install_update, style="primary",
                                           size=12, state="disabled")
        self.update_later_button = ui.Btn(p, "Later", self.snooze_update, style="ghost", size=12, state="disabled")
        self.update_cancel_button = ui.Btn(p, "Cancel download", self.cancel_update_download, style="ghost",
                                           size=12, state="disabled")
        self.release_button = ui.Btn(p, "Open latest release", self.open_release_page, style="ghost", size=12)

        self.about_card = ui.Card(p)
        self.about_cap = ui.Label(p, "ABOUT", fg=ui.CYAN, size=10, bold=True)
        self.save_location_label = ui.Label(p, f"Saving to  {self.download_dir}", fg=ui.MUTED, size=11,
                                            ellipsis=True, relayout=False)
        self.about_note = ui.Label(p, "Use only for content you own or have permission to download.", fg=ui.FAINT,
                                   size=11)
        self.diag_button = ui.Btn(p, "Diagnostics", self.open_diagnostics, style="secondary", size=11)
        self.dev_button = ui.Btn(p, "Developed by Md Omar Faruk  ↗",
                                 lambda: webbrowser.open("https://github.com/umar-vai"), style="ghost", size=11)

    def _layout_updates(self, w: int, h: int) -> int:
        x0 = px(24)
        cw = w - 2 * x0 - px(4)
        pad = px(22)
        y = px(20)
        self.up_title.place(x0, y)
        self.up_sub.place(x0, y + px(34))
        top = y + px(72)
        inner_w = cw - 2 * pad
        self.update_status_label.place(x0 + pad, top + px(24))
        cb = self.update_check_button.natural_w(minimum=150)
        self.update_check_button.place(x0 + cw - pad - cb, top + px(16), cb, px(40))
        cy = top + px(70)
        cy += self.update_detail_label.place(x0 + pad, cy, inner_w) + px(8)
        if self.update_notes_label.text:
            cy += self.update_notes_label.place(x0 + pad, cy, inner_w) + px(8)
        self.update_progress.place(x0 + pad, cy + px(6), inner_w)
        cy += px(32)
        bx = x0 + pad
        for button in (self.update_action_button, self.update_later_button, self.update_cancel_button,
                       self.release_button):
            bw = button.natural_w(minimum=96)
            button.place(bx, cy, bw, px(42))
            bx += bw + px(8)
        card_h = (cy + px(42) + pad) - top
        self.update_card_bg.place(x0, top, cw, card_h)

        top = top + card_h + px(16)
        self.about_cap.place(x0 + pad, top + px(22))
        self.save_location_label.place(x0 + pad, top + px(52), inner_w)
        self.about_note.place(x0 + pad, top + px(76))
        dw = self.diag_button.natural_w(minimum=110)
        self.diag_button.place(x0 + pad, top + px(112), dw, px(38))
        self.dev_button.place(x0 + pad + dw + px(8), top + px(112), self.dev_button.natural_w(), px(38))
        about_h = px(112) + px(38) + pad
        self.about_card.place(x0, top, cw, about_h)
        return int(top + about_h + px(24))

    def _open_settings_dialog(self, tab: str | None = None) -> None:
        window = getattr(self, "settings_window", None)
        try:
            alive = window is not None and window.winfo_exists()
        except Exception:
            alive = False
        if alive:
            window.deiconify()
            window.lift()
            window.focus_force()
            if tab and self.settings_panel is not None:
                self.settings_panel.tabs.set(tab)
            return
        window = ctk.CTkToplevel(self)
        window.title("Options")
        window.configure(fg_color=ui.BG)
        window.transient(self)
        window.geometry("920x700")
        window.minsize(760, 560)
        self.settings_window = window

        def close() -> None:
            panel = self.settings_panel
            if panel is not None and not panel.can_leave():
                return
            self.settings_panel = None
            self.settings_window = None
            window.destroy()

        panel = self._build_settings_panel(window, close)
        panel.pack(fill="both", expand=True)
        if tab:
            panel.tabs.set(tab)
        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda _e: close())
        if self._icon_file:
            window.after(260, lambda: window.winfo_exists() and window.iconbitmap(self._icon_file))
        window.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - window.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - window.winfo_height()) // 4)
        tk.Wm.geometry(window, f"+{max(0, x)}+{max(0, y)}")
        window.after(60, window.focus_force)

    # =========================================================== navigation
    def show_page(self, name: str) -> None:
        """Older code paths ask for 'pages'; in this window they are dialogs or categories."""
        if name == "download":
            self.open_add_url()
        elif name == "batch":
            self.batch_dialog.open()
            self.batch_page.focus()
        elif name == "updates":
            self.about_dialog.open()
        elif name == "settings":
            self._open_settings_dialog()
        elif name == "history":
            self._show_category("finished")
        elif name in {"queue", "list"} and self._category in {"finished"}:
            self._show_category("all")
        self._schedule_download_activity_refresh(30)

    def open_add_url(self) -> None:
        if not self.url_var.get().strip():
            try:
                text = self.clipboard_get().strip()
            except Exception:
                text = ""
            if text.startswith(("http://", "https://")) and "\n" not in text and len(text) < 2000:
                self.url_var.set(text)
                self._on_url_changed()
        self.add_dialog.open()
        self.after(60, self.url_entry.focus_set)

    def _hide_window(self) -> None:
        if getattr(self, "tray", None) is not None:
            self.hide_to_tray()
        else:
            self.iconify()

    def _show_category(self, key: str) -> None:
        self.category_tree.select(key)
        self._select_category(key)

    def _select_category(self, key: str) -> None:
        self._category = key
        self.list_title.configure(text=self.category_tree.title_of(key))
        self._render_download_activity()

    def _search_changed(self, value: str) -> None:
        self._query = value
        self._schedule_download_activity_refresh(60)

    def _sort_by(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = column
            self._sort_desc = column in {"size", "speed", "last_try", "added"}
        self.table.mark_sort(self._sort_column, self._sort_desc)
        self.settings["list_sort"] = [self._sort_column, self._sort_desc]
        self._persist_settings()
        self._render_download_activity()

    def _heading_press(self, event: Any) -> None:
        self._heading_drag = self.table.tree.identify_region(event.x, event.y) == "separator"

    def _heading_release(self, _event: Any) -> None:
        if self._heading_drag:
            self._heading_drag = False
            self.settings["list_widths"] = self.table.widths()
            self._persist_settings()

    def _apply_columns(self, save: bool = True) -> None:
        visible = [key for key in dl.COLUMN_KEYS if key in self._column_vars and self._column_vars[key].get()]
        visible = dl.normalize_columns(visible) if visible else []
        self.table.set_visible(visible)
        if save:
            self.settings["list_columns"] = visible
            self._persist_settings()

    def _reset_columns(self) -> None:
        for key, var in self._column_vars.items():
            var.set(key in dl.DEFAULT_VISIBLE)
        self.table.set_widths({key: width for key, _t, width, _a in dl.COLUMNS})
        self.settings["list_widths"] = {}
        self._apply_columns()

    def _apply_panes(self, save: bool = True) -> None:
        show_tree = bool(self._show_categories_var.get())
        panes = [str(p) for p in self.body.panes()]
        if show_tree and str(self.category_tree) not in panes:
            self.body.add(self.category_tree, before=self._right, minsize=px(160), width=px(214), stretch="never")
        elif not show_tree and str(self.category_tree) in panes:
            self.body.forget(self.category_tree)
        if self._show_details_var.get():
            self.details.pack(side="bottom", fill="x", before=self.table)
        else:
            self.details.pack_forget()
        if save:
            self.settings["show_categories"] = show_tree
            self.settings["show_details"] = bool(self._show_details_var.get())
            self._persist_settings()

    def _choose_theme(self) -> None:
        name = theme.normalize_theme(self._theme_var.get())
        if name == theme.normalize_theme(self.settings.get("theme")):
            return
        self.settings["theme"] = name
        self._persist_settings()
        messagebox.showinfo("Media Downloader", f"The {name.lower()} theme will be used the next time "
                                                "Media Downloader starts.", parent=self)

    def refresh_list(self) -> None:
        self._invalidate_history()
        self._sync_recent_file()

    # ======================================================== status / mode
    def _sync_mode(self, value: str | None = None) -> None:
        mode = value or self.mode_var.get()
        is_video = mode == "Video"
        self.mode_var.set(mode)
        self.video_quality.configure(state="normal" if is_video else "disabled")
        self.audio_format.configure(state="disabled" if is_video else "normal")
        self.audio_quality.configure(state="disabled" if is_video else "normal")
        self.quality_label.configure(text_color=ui.MUTED if is_video else ui.FAINT)
        self.audio_format_label.configure(text_color=ui.FAINT if is_video else ui.MUTED)
        self.audio_quality_label.configure(text_color=ui.FAINT if is_video else ui.MUTED)
        self.download_button.configure(text="Download video" if is_video else "Download audio")
        self.output_hint.configure(
            text="MP4 video • FFmpeg merge when needed" if is_video else "Audio only • choose format and bitrate"
        )

    def _set_status(self, text: str, kind: str = "ready") -> None:
        bg, fg, chip = STATUS_STYLES.get(kind, STATUS_STYLES["ready"])
        self.status_chip.configure(text=chip, fg_color=bg, text_color=fg)
        self.status_label.configure(text=text)
        self.status_label.show(bool(text))
        self.statusbar.set_message(text, fg)

    # ============================================================ list data
    def _invalidate_history(self) -> None:
        self._history_rows = None
        self._schedule_download_activity_refresh(40)

    def _history_rows_cached(self) -> list[dict[str, Any]]:
        if self._history_rows is None:
            entries = self.history_store.list()
            self._history_entries = {entry["id"]: entry for entry in entries}
            self._history_rows = [dl.history_row(entry) for entry in entries]
            self._history_built_at = time.monotonic()
        return self._history_rows

    def _schedule_download_activity_refresh(self, delay_ms: int = 120) -> None:
        if self._download_activity_after_id is not None:
            return

        def run() -> None:
            self._download_activity_after_id = None
            self._render_download_activity()

        self._download_activity_after_id = self.after(max(30, int(delay_ms)), run)

    def _render_download_activity(self) -> None:
        if not hasattr(self, "table"):
            return
        snapshots = self.download_queue.snapshot()
        if self._discard_ids:
            removed = False
            for item in snapshots:
                if item["id"] in self._discard_ids and item.get("status") in {"completed", "failed", "cancelled"}:
                    self.download_queue.remove(item["id"])
                    self._discard_ids.discard(item["id"])
                    removed = True
            if removed:
                snapshots = self.download_queue.snapshot()
        active = self.download_queue.running_count()
        queued = self.download_queue.queued_count()
        try:
            hidden = self.state() in {"withdrawn", "iconic"}
        except Exception:
            hidden = False
        if not hidden:
            with self._download_progress_lock:
                metrics = dict(self._latest_download_metrics)
            queue_rows = [dl.queue_row(item, metrics.get(item["id"])) for item in snapshots]
            rows = dl.merge_rows(queue_rows, self._history_rows_cached())
            self._rows = rows
            self._rows_by_key = {row["key"]: row for row in rows}
            self._refresh_view()
        self._after_activity_render(snapshots, active, queued)

    def _refresh_view(self) -> None:
        rows = self._rows
        self.category_tree.update_counts(dl.category_counts(rows), dl.queue_groups(rows))
        shown = dl.sort_rows(dl.filter_rows(rows, self._category, self._query), self._sort_column, self._sort_desc)

        before = self.table.selected()
        lost = [key for key in before if key not in self._rows_by_key]
        self.table.sync(shown)
        if lost:
            # A finished download moves from the live queue to the history: keep it selected.
            old_ids = {key[2:] for key in lost if key.startswith("q:")}
            follow = [row["key"] for row in shown if row["request_id"] in old_ids]
            if follow:
                self.table.select([key for key in before if key in self._rows_by_key] + follow)
        self._shown_keys = [row["key"] for row in shown]

        total = len(shown)
        self.list_count.configure(text=f"{total} item{'s' if total != 1 else ''}" if total else "")
        if total:
            self.table.show_empty("", "")
        elif self._query:
            self.table.show_empty("No matches", f"No downloads match “{self._query}”.")
        else:
            title, text = EMPTY_TEXT.get(self._category) or (
                "Nothing here yet", "Downloads of this type appear here.")
            self.table.show_empty(title, text)

        totals = dl.totals(rows)
        parts = []
        if totals["downloading"]:
            parts.append(f"{totals['downloading']} downloading")
        if totals["queued"]:
            parts.append(f"{totals['queued']} queued")
        if totals["paused"]:
            parts.append(f"{totals['paused']} stopped")
        if totals["failed"]:
            parts.append(f"{totals['failed']} failed")
        self.statusbar.set_totals("  •  ".join(parts) or "No active downloads", totals["speed_text"])
        server = getattr(self, "extension_server", None)
        ext = "Browser extension: on" if server is not None else "Browser extension: off"
        if self.statusbar.extension.cget("text") != ext:
            self.statusbar.extension.configure(text=ext)
        self._update_selection_ui(totals)

    def _selected_rows(self) -> list[dict[str, Any]]:
        return [self._rows_by_key[key] for key in self.table.selected() if key in self._rows_by_key]

    def _update_selection_ui(self, totals: dict[str, Any] | None = None) -> None:
        rows = self._selected_rows()
        actions = dl.available_actions(rows)
        totals = totals or dl.totals(self._rows)
        buttons = self.toolbar.buttons
        buttons["resume"].set_enabled("resume" in actions)
        buttons["stop"].set_enabled("stop" in actions)
        buttons["delete"].set_enabled("delete" in actions)
        buttons["start_all"].set_enabled(bool(totals["paused"]))
        buttons["stop_all"].set_enabled(bool(totals["downloading"] or totals["queued"]))
        buttons["delete_done"].set_enabled(any(row["status"] in dl.FINISHED for row in self._rows))
        self.details.show(rows[0] if len(rows) == 1 else None, len(rows), dl.selection_summary(rows))

    # ============================================================== actions
    def select_all(self) -> None:
        self.table.select(list(self._shown_keys))

    def _row_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        """A history-style dict for the Properties dialog."""
        if row["source"] == "history" and row["history_id"] in self._history_entries:
            return dict(self._history_entries[row["history_id"]])
        status = {"complete": "completed"}.get(row["status"], row["status"])
        return {
            "id": row["request_id"], "title": row["name"], "status": status, "file_name": row["name"],
            "file_path": row["path"], "download_dir": row["save_to"] if not row["path"] else str(Path(row["path"]).parent),
            "file_size": row["size"], "mode": row["mode"], "quality": row["quality"], "website": row["website"],
            "page_url": row["page_url"], "download_url": row["url"], "error": row["error"],
        }

    def _open_row(self, row: dict[str, Any]) -> None:
        path = Path(row["path"])
        if not row["path"] or not path.exists():
            messagebox.showwarning("Media Downloader", "This file is no longer on the disk.", parent=self)
            self._invalidate_history()
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Media Downloader", f"Could not open the file.\n\n{exc}", parent=self)

    def _open_folder_row(self, row: dict[str, Any]) -> None:
        target = row["path"] if row["path"] and Path(row["path"]).exists() else row["save_to"] or str(self.download_dir)
        reveal_in_folder(target)

    def _resume_row(self, row: dict[str, Any]) -> None:
        request_id = row["request_id"]
        live = self.download_queue.get(request_id) if request_id else None
        if live is not None and live.get("status") == "paused":
            self.resume_download(request_id)
        elif live is not None and live.get("status") in {"failed", "cancelled"}:
            self.retry_download(request_id)
        elif row["source"] == "history" and row["status"] in {"failed", "cancelled"}:
            entry = self._history_entries.get(row["history_id"])
            if entry:
                self.redownload_from_history(entry, resume=True)

    def _redownload_row(self, row: dict[str, Any]) -> None:
        if row["source"] == "history":
            entry = self._history_entries.get(row["history_id"])
            if entry:
                self.redownload_from_history(entry, resume=False)
        elif row["status"] in {"failed", "cancelled"}:
            self.retry_download(row["request_id"])

    def open_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1 and "open" in dl.available_actions(rows):
            self._open_row(rows[0])

    def open_selected_folder(self) -> None:
        rows = self._selected_rows()
        if rows:
            self._open_folder_row(rows[0])

    def edit_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1 and "edit" in dl.available_actions(rows):
            self.open_editor(Path(rows[0]["path"]))

    def resume_selected(self) -> None:
        for row in self._selected_rows():
            if row["status"] in dl.RESUMABLE:
                self._resume_row(row)

    def stop_selected(self) -> None:
        for row in self._selected_rows():
            if row["source"] == "queue" and row["status"] in dl.STOPPABLE:
                self.pause_download(row["request_id"])

    def _toggle_selected(self) -> None:
        actions = dl.available_actions(self._selected_rows())
        if "resume" in actions:
            self.resume_selected()
        elif "stop" in actions:
            self.stop_selected()

    def cancel_selected(self) -> None:
        for row in self._selected_rows():
            if row["source"] == "queue" and row["status"] in dl.STOPPABLE | {"paused"}:
                self.cancel_download(row["request_id"])

    def redownload_selected(self) -> None:
        for row in self._selected_rows():
            if row["status"] in dl.FINISHED | {"failed", "cancelled"} and row["url"]:
                self._redownload_row(row)

    def copy_selected_links(self) -> None:
        links = [row["url"] for row in self._selected_rows() if row["url"]]
        if links:
            self.clipboard_clear()
            self.clipboard_append("\n".join(links))
            self._set_status(f"Copied {len(links)} link{'s' if len(links) != 1 else ''}", "ready")

    def _copy_text(self, text: str, what: str) -> None:
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status(f"Copied the {what}", "ready")

    def details_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1 and rows[0]["has_details"]:
            self.show_failure_details(rows[0]["request_id"])

    def properties_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        row = rows[0]

        def act(action: str) -> None:
            if action == "open":
                self._open_row(row)
            elif action == "reveal":
                self._open_folder_row(row)
            elif action == "page" and row["page_url"].startswith(("http://", "https://")):
                webbrowser.open(row["page_url"])
            elif action == "resume":
                self._resume_row(row)
            elif action == "redownload" and row["status"] in dl.FINISHED | {"failed", "cancelled"}:
                self._redownload_row(row)

        PropertiesDialog(self, self._row_entry(row), on_action=act)

    def _details_or_properties(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1 and rows[0]["has_details"]:
            self.details_selected()
        else:
            self.properties_selected()

    def _open_default(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        row = rows[0]
        if row["status"] == "complete" and row["file_exists"]:
            self._open_row(row)
        elif row["has_details"]:
            self.show_failure_details(row["request_id"])
        else:
            self.properties_selected()

    def delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        files = [row for row in rows if row["status"] == "complete" and row["file_exists"]]
        ok, delete_files = ConfirmDelete.ask(self, count=len(rows), files=len(files))
        if ok:
            self._delete_rows(rows, delete_files)

    def delete_completed(self) -> None:
        rows = [row for row in self._rows if row["status"] in dl.FINISHED]
        if not rows:
            return
        files = [row for row in rows if row["file_exists"]]
        ok, delete_files = ConfirmDelete.ask(self, count=len(rows), files=len(files), finished_only=True)
        if ok:
            self._delete_rows(rows, delete_files)

    def remove_missing(self) -> None:
        removed = self.history_store.prune_missing()
        self._invalidate_history()
        self._set_status(f"Removed {removed} missing file{'s' if removed != 1 else ''} from the list", "ready")

    def _delete_rows(self, rows: list[dict[str, Any]], delete_files: bool) -> None:
        history_ids: list[str] = []
        problems: list[str] = []
        for row in rows:
            request_id = row["request_id"]
            if row["source"] == "queue":
                if row["status"] in dl.STOPPABLE | {"paused", "stopping"}:
                    self._discard_ids.add(request_id)
                    self.cancel_download(request_id)
                else:
                    self.download_queue.remove(request_id)
            else:
                history_ids.append(row["history_id"])
                if request_id:
                    self.download_queue.remove(request_id)
            if delete_files and row["status"] == "complete" and row["file_exists"] and row["path"]:
                try:
                    Path(row["path"]).unlink(missing_ok=True)
                except OSError as exc:
                    problems.append(f"{Path(row['path']).name}: {exc}")
        if history_ids:
            self.history_store.remove_many(history_ids)
        self._invalidate_history()
        self._sync_recent_file()
        count = len(rows)
        self._set_status(f"Deleted {count} download{'s' if count != 1 else ''} from the list", "ready")
        if problems:
            messagebox.showerror("Media Downloader", "Some files could not be deleted:\n\n" + "\n".join(problems[:12]),
                                 parent=self)

    # ------------------------------------------------------------ menus
    def _show_context_menu(self, event: Any, on_row: bool) -> None:
        menu = tk.Menu(self, tearoff=0)
        x, y = int(getattr(event, "x_root", 0) or 0), int(getattr(event, "y_root", 0) or 0)
        if not x and not y:
            x, y = self.table.winfo_rootx() + px(40), self.table.winfo_rooty() + px(40)
        if not on_row or not self._selected_rows():
            menu.add_command(label="Add URL…", accelerator="Ctrl+N", command=self.open_add_url)
            menu.add_command(label="Add batch download…", accelerator="Ctrl+B", command=self.open_batch_downloads)
            menu.add_separator()
            menu.add_command(label="Select all", accelerator="Ctrl+A", command=self.select_all)
            menu.add_command(label="Refresh", accelerator="F5", command=self.refresh_list)
        else:
            rows = self._selected_rows()
            row = rows[0]
            actions = dl.available_actions(rows)

            def add(label: str, command: Any, key: str | None = None, accelerator: str = "") -> None:
                menu.add_command(label=label, command=command, accelerator=accelerator,
                                 state="normal" if key is None or key in actions else "disabled")

            add("Open", self.open_selected, "open", "Enter")
            add("Open folder", self.open_selected_folder, "open_folder")
            add("Open in media editor", self.edit_selected, "edit")
            menu.add_separator()
            add("Resume", self.resume_selected, "resume")
            add("Stop", self.stop_selected, "stop")
            add("Cancel download", self.cancel_selected, "cancel")
            add("Download again", self.redownload_selected, "redownload")
            menu.add_separator()
            add("Copy download link", self.copy_selected_links, "copy_link", "Ctrl+C")
            add("Copy web page link", lambda: self._copy_text(row["page_url"], "web page link"), "open_page")
            add("Open web page", lambda: webbrowser.open(row["page_url"]), "open_page")
            menu.add_separator()
            add("Delete…", self.delete_selected, "delete", "Del")
            menu.add_separator()
            if "details" in actions:
                add("Error details…", self.details_selected, "details")
            add("Properties", self.properties_selected, "properties", "Alt+Enter")
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _browser_menu(self) -> None:
        button = self.toolbar.buttons["browsers"]
        menu = tk.Menu(self, tearoff=0)
        server = getattr(self, "extension_server", None)
        status = (f"Connected: listening on port {server.port}" if server is not None
                  else "Browser extension link is off")
        menu.add_command(label=status, state="disabled")
        menu.add_separator()
        menu.add_command(label="Install browser extension…", command=self.export_browser_extension)
        menu.add_command(label="Copy pairing token",
                         command=lambda: self._copy_text(str(self.settings.get("extension_token") or ""),
                                                         "pairing token"))
        menu.add_command(label="Browser settings…", command=lambda: self.open_settings(tab="Browser"))
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    # ------------------------------------------------------- after queueing
    def _after_link_queued(self, request_id: str, name: str) -> None:
        self.add_dialog.close()
        self.clear_form()
        self._set_status(f"Added to downloads: {name}", "ready")
        if self._category not in {"all", "unfinished", "queue:active", dl.category_for(name, self.mode_var.get())}:
            self._show_category("all")
        self._render_download_activity()
        self.table.select([f"q:{request_id}"])

    def _after_batch_queued(self, batch_id: str) -> None:
        self.batch_dialog.close()
        self._render_download_activity()
        key = f"queue:{batch_id}"
        self._show_category(key if self.category_tree.tree.exists(key) else "all")

    # ============================================================ thumbnail
    def _reset_thumbnail_label(self) -> None:
        self.thumbnail_image = None
        self.thumb.set_image(None)

    def _apply_thumbnail(self, raw: bytes | None) -> None:
        if not raw:
            self._reset_thumbnail_label()
            return
        try:
            width, height = px(THUMB_W) - 2, px(THUMB_H) - 2
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.thumbnail((width, height), Image.Resampling.BILINEAR)
            canvas = Image.new("RGB", (width, height), theme.rgb(ui.SURFACE_2))
            canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
            photo = ImageTk.PhotoImage(canvas)
            self.thumbnail_image = photo
            self.thumb.set_image(photo)
        except Exception:
            self._reset_thumbnail_label()
