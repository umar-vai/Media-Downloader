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
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from PIL import Image, ImageTk

import download_list as dl
import file_download
import native
import theme
import ui_kit as ui
from batch_download import parse_batch_urls
from batch_page import BatchSection
from history_window import PropertiesDialog
from icons import IconSet
from partial_files import delete_partial_files, find_partial_files, path_size, remove_path, scan_partial_files
from main_window import (CategoryTree, CleanupDialog, ConfirmDelete, DetailsPane, DownloadTable, SearchBox, StatusBar,
                         Toolbar, UpdateBadge, setup_style, sysfont, toolbar_states, totals_summary)
from theme import C
from ui_kit import px
from version import APP_VERSION

THUMB_W, THUMB_H = 184, 104
_ICON_FILE = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
STATUS_COLORS = {
    "ready": C["SUCCESS"], "working": C["ACCENT"], "success": C["SUCCESS"], "cancelled": C["WARNING"],
    "error": C["DANGER"],
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
        self._history_rows: list[dict[str, Any]] = []
        self._history_entries: dict[str, dict[str, Any]] = {}
        self._history_built_at = 0.0
        self._history_generation = 0
        self._history_loading = False
        self._discard_ids: set[str] = set()
        # request id -> (folder, name) whose partial files are removed once the worker has stopped.
        self._discard_cleanup: dict[str, tuple[str, str]] = {}
        self._heading_drag = False
        self._shown_keys: list[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_menubar()
        self._build_toolbar()
        ttk.Separator(self, orient="horizontal").grid(row=1, column=0, sticky="ew")
        self._build_body()
        self.statusbar = StatusBar(self)
        self.statusbar.grid(row=3, column=0, sticky="ew")
        self.statusbar.version.configure(text=f"v{APP_VERSION}")
        self.after(1500, self._refresh_extension_status)

        self._build_add_dialog()
        self._build_batch_dialog()
        self._build_about_dialog()
        self._bind_shortcuts()

        self._sync_mode(self.mode_var.get())
        self._invalidate_history()
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
        file.add_command(label="Screen recorder…", command=self.open_screen_recorder)
        file.add_command(label="Audio recorder (calls, meetings)…", command=self.open_audio_recorder)
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
        downloads.add_command(label="Clean up list…", command=self.cleanup_list)
        downloads.add_command(label="Clean up partial files…", command=self.clean_partial_files)
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
        bar.add("cleanup", "Clean Up", "cleanup", self.cleanup_list,
                "Remove missing, cancelled and failed downloads in one go")
        bar.separator()
        bar.add("options", "Options", "settings", self.open_settings, "Settings")
        bar.add("browsers", "Browsers", "browser", self._browser_menu, "Browser extension")
        bar.add("editor", "Editor", "editor", lambda: self.open_editor(), "Cut, convert and export media files")
        bar.add("recorder", "Record", "record", self.open_screen_recorder,
                "Record the screen (full screen or an area) with sound")
        bar.add("audio_recorder", "Audio", "mic", self.open_audio_recorder,
                "Record a call or meeting: the other side's voice and yours, as MP3 / WAV")
        self.toolbar = bar
        self.top_update_button = UpdateBadge(bar.right, self.icons, lambda: self.show_page("updates"),
                                              check=lambda: self.check_for_updates(manual=False, inline=True),
                                              version=APP_VERSION)

    # ----------------------------------------------------------------- body
    def _build_body(self) -> None:
        self.body = tk.PanedWindow(self, orient="horizontal", sashwidth=px(4), bg=C["BG"], bd=0,
                                   sashrelief="flat", showhandle=False, opaqueresize=True)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.category_tree = CategoryTree(self.body, self.icons, self._select_category)
        right = tk.Frame(self.body, bg=C["BG"])
        self._right = right
        self.body.add(self.category_tree, minsize=px(160), width=px(214), stretch="never")
        self.body.add(right, minsize=px(460), stretch="always")

        # A plain one-line header (category name, item count, search), like a Windows list pane.
        header = tk.Frame(right, bg=C["BG"])
        header.pack(side="top", fill="x")
        self.list_title = tk.Label(header, text="All Downloads", bg=C["BG"], fg=C["TEXT"],
                                   font=sysfont(True), anchor="w")
        self.list_title.pack(side="left", padx=(px(2), px(6)), pady=px(2))
        self.list_count = tk.Label(header, text="", bg=C["BG"], fg=C["FAINT"], font=sysfont(), anchor="w")
        self.list_count.pack(side="left", pady=px(2))
        self.search = SearchBox(header, self._search_changed)
        self.search.pack(side="right", padx=(0, px(3)), pady=px(2))

        self.details = DetailsPane(right, self.icons, {
            "toggle": self._toggle_selected, "cancel": self.cancel_selected, "open": self.open_selected,
            "open_folder": self.open_selected_folder, "more": self._details_or_properties,
        })
        self.details.pack(side="bottom", fill="x")
        self.table = DownloadTable(right, self.icons, on_select=self._update_selection_ui, on_open=self._open_default,
                                   on_context=self._show_context_menu, on_sort=self._sort_by,
                                   on_delete=self.delete_selected, drag_paths=self._selected_file_paths)
        self.table.pack(side="top", fill="both", expand=True)
        self.table.set_widths(self.settings.get("list_widths") or {})
        self.table.tree.bind("<ButtonPress-1>", self._heading_press, add="+")
        self.table.tree.bind("<ButtonRelease-1>", self._heading_release, add="+")
        self.table.tree.bind("<Alt-Return>", lambda _e: self.properties_selected())
        self.table.tree.bind("<Control-c>", lambda _e: self.copy_selected_links())
        self.table.tree.bind("<Control-C>", lambda _e: self.copy_selected_files())  # Ctrl+Shift+C
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
        if time.monotonic() - self._history_built_at > 20:
            self._invalidate_history()

    # ============================================================== dialogs
    def _build_add_dialog(self) -> None:
        dialog = native.HiddenDialog(self, "Add URL", icon=self._icon_file)
        self.add_dialog = dialog
        body = dialog.body
        body.grid_columnconfigure(1, weight=1)

        # address
        ttk.Label(body, text="Address:").grid(row=0, column=0, sticky="w", padx=(0, native.GAP))
        self.url_entry = ttk.Entry(body, textvariable=self.url_var, width=62)
        self.url_entry.grid(row=0, column=1, sticky="ew")
        self.url_entry.bind("<Return>", lambda _e: (self.analyze_media(), "break")[1])
        self.url_entry.bind("<KeyRelease>", self._on_url_changed)
        links = ttk.Frame(body)
        links.grid(row=0, column=2, sticky="e", padx=(native.GAP, 0))
        self.paste_button = native.Button(links, text="Paste", command=self.paste_from_clipboard, width=9)
        self.paste_button.pack(side="left")
        self.read_button = native.Button(links, text="Analyze", command=self.analyze_media, width=11)
        self.read_button.pack(side="left", padx=(native.GAP, 0))
        self.cancel_analysis_button = native.Button(links, text="Stop", command=self.cancel_analysis, width=9,
                                                    state="disabled")
        self.cancel_analysis_button.pack(side="left", padx=(native.GAP, 0))
        native.Text(body, "Paste a link to a video, audio or file. It is read automatically.",
                    style="Hint.TLabel").grid(row=1, column=1, columnspan=2, sticky="w", pady=(2, native.PAD))

        # what the link is
        info = native.group(body, "File information", row=2, column=0, columnspan=3, sticky="ew")
        info.grid_columnconfigure(1, weight=1)
        thumb_box = tk.Frame(info, width=px(THUMB_W), height=px(THUMB_H), bg=C["SURFACE_3"],
                             highlightthickness=1, highlightbackground=C["BORDER"])
        thumb_box.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, native.PAD))
        thumb_box.grid_propagate(False)
        thumb_box.pack_propagate(False)
        self.thumb = _Thumb(thumb_box)
        self.media_badge = native.Text(info, "WAITING FOR LINK", style="Hint.TLabel")
        self.media_badge.grid(row=0, column=1, sticky="w")
        self.title_label = native.Text(info, "Analyze a link to see its details", style="Title.TLabel",
                                       wrap=px(440))
        self.title_label.grid(row=1, column=1, sticky="ew", pady=(2, 2))
        self.meta_label = native.Text(info, "Title, creator, website and length appear here.", wrap=px(440))
        self.meta_label.grid(row=2, column=1, sticky="ew")
        self.media_action_hint = native.Text(info, "", style="Hint.TLabel", wrap=px(440))
        self.media_action_hint.grid(row=3, column=1, sticky="ew", pady=(native.GAP, 0))

        # how to save it
        save = native.group(body, "Save as", row=3, column=0, columnspan=3, sticky="ew", pady=(native.PAD, 0))
        save.grid_columnconfigure(1, weight=1)
        ttk.Label(save, text="Type:").grid(row=0, column=0, sticky="w", padx=(0, native.PAD), pady=2)
        kinds = ttk.Frame(save)
        kinds.grid(row=0, column=1, columnspan=3, sticky="w", pady=2)
        for value in ("Video", "Audio"):
            ttk.Radiobutton(kinds, text=value, value=value, variable=self.mode_var,
                            command=self._sync_mode).pack(side="left", padx=(0, native.PAD * 2))
        self.quality_label = native.Text(save, "Video quality:")
        self.quality_label.grid(row=1, column=0, sticky="w", padx=(0, native.PAD), pady=2)
        self.video_quality = _Combo(save, self.video_quality_var, ["Best available", "1080p", "720p", "480p", "360p"])
        self.video_quality.grid(row=1, column=1, sticky="w", pady=2)
        self.audio_format_label = native.Text(save, "Audio format:")
        self.audio_format_label.grid(row=2, column=0, sticky="w", padx=(0, native.PAD), pady=2)
        audio = ttk.Frame(save)
        audio.grid(row=2, column=1, columnspan=3, sticky="w", pady=2)
        self.audio_format = _Combo(audio, self.audio_format_var, ["MP3", "M4A", "FLAC", "OPUS", "WAV"], width=8)
        self.audio_format.pack(side="left")
        self.audio_quality_label = native.Text(audio, "Bitrate (kbps):")
        self.audio_quality_label.pack(side="left", padx=(native.PAD * 2, native.GAP))
        self.audio_quality = _Combo(audio, self.audio_quality_var, ["128", "192", "256", "320"], width=6)
        self.audio_quality.pack(side="left")
        ttk.Label(save, text="File name:").grid(row=3, column=0, sticky="w", padx=(0, native.PAD), pady=2)
        self.name_entry = ttk.Entry(save, textvariable=self.name_var)
        self.name_entry.grid(row=3, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(save, text="Save to:").grid(row=4, column=0, sticky="w", padx=(0, native.PAD), pady=2)
        self.folder_entry = ttk.Entry(save, textvariable=self.download_dir_var, state="readonly")
        self.folder_entry.grid(row=4, column=1, sticky="ew", pady=2)
        self.choose_folder_button = native.Button(save, text="Browse…", command=self.choose_download_folder, width=10)
        self.choose_folder_button.grid(row=4, column=2, padx=(native.GAP, 0))
        self.reset_folder_button = native.Button(save, text="Default", command=self.reset_download_folder, width=9)
        self.reset_folder_button.grid(row=4, column=3, padx=(native.GAP, 0))
        self.output_hint = native.Text(save, "MP4 video • FFmpeg merge when needed", style="Hint.TLabel")
        self.output_hint.grid(row=5, column=1, columnspan=3, sticky="w", pady=(2, 0))
        self.open_editor_after_check = ttk.Checkbutton(body, text="Open in the media editor after downloading",
                                                       variable=self.open_editor_after_var)
        self.open_editor_after_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(native.PAD, 0))

        self.status_label = dialog.status
        self.add_close_button = dialog.add_button("Cancel", dialog.close)
        self.download_button = native.Button(dialog.buttons, text="Download video", command=self.download, width=16,
                                             state="disabled", default="active")
        self.download_button.pack(side="right", padx=(native.GAP, 0))

    def _build_batch_dialog(self) -> None:
        dialog = native.HiddenDialog(self, "Add batch download", resizable=True, icon=self._icon_file)
        self.batch_dialog = dialog
        self.batch_page = BatchSection(dialog, on_enqueue=self._queue_links, settings=self.settings,
                                       on_queued=self._after_batch_queued)

    def _build_about_dialog(self) -> None:
        dialog = native.HiddenDialog(self, "Updates & about", icon=self._icon_file)
        self.about_dialog = dialog
        body = dialog.body
        body.grid_columnconfigure(1, weight=1)
        try:
            logo = self.icons.tool("folder", px(40))
            if self._icon_file:
                from PIL import Image as _Image
                with _Image.open(self._icon_file) as ico:
                    ico.size = max(ico.info.get("sizes") or {(48, 48)})
                    picture = ico.convert("RGBA").resize((px(40), px(40)), _Image.Resampling.LANCZOS)
                logo = ImageTk.PhotoImage(picture, master=dialog)
            self._about_logo = logo
            ttk.Label(body, image=logo).grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, native.PAD))
        except Exception:
            pass
        ttk.Label(body, text="Media Downloader", style="Big.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Version {APP_VERSION}", style="Hint.TLabel").grid(row=1, column=1, sticky="w")

        box = native.group(body, "Updates", row=2, column=0, columnspan=2, sticky="ew", pady=(native.PAD, 0))
        box.grid_columnconfigure(0, weight=1)
        self.update_status_label = native.Text(box, f"Current version: v{APP_VERSION}", style="Title.TLabel")
        self.update_status_label.grid(row=0, column=0, sticky="w")
        self.update_check_button = native.Button(box, text="Check for updates", width=18,
                                                  command=lambda: self.check_for_updates(manual=True))
        self.update_check_button.grid(row=0, column=1, sticky="e")
        self.update_detail_label = native.Text(
            box, "Automatic update checks are enabled." if self.auto_check_updates_var.get()
            else "Automatic update checks are disabled.", wrap=px(460))
        self.update_detail_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(native.GAP, 0))
        self.update_notes_label = native.Text(box, "", style="Hint.TLabel", wrap=px(460))
        self.update_notes_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(native.GAP, 0))
        self.update_progress = native.Progress(box)
        self.update_progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(native.PAD, 0))
        actions = ttk.Frame(box)
        actions.grid(row=4, column=0, columnspan=2, sticky="w", pady=(native.PAD, 0))
        self.update_action_button = native.Button(actions, text="Update now", command=self.download_or_install_update,
                                                  state="disabled", width=14)
        self.update_action_button.pack(side="left")
        self.update_later_button = native.Button(actions, text="Later", command=self.snooze_update, state="disabled",
                                                 width=9)
        self.update_later_button.pack(side="left", padx=(native.GAP, 0))
        self.update_cancel_button = native.Button(actions, text="Cancel download", width=15, state="disabled",
                                                  command=self.cancel_update_download)
        self.update_cancel_button.pack(side="left", padx=(native.GAP, 0))
        self.release_button = native.Button(actions, text="Open latest release", command=self.open_release_page,
                                            width=18)
        self.release_button.pack(side="left", padx=(native.GAP, 0))

        about = native.group(body, "About", row=3, column=0, columnspan=2, sticky="ew", pady=(native.PAD, 0))
        about.grid_columnconfigure(0, weight=1)
        self.save_location_label = native.Text(about, f"Saving to  {self.download_dir}", wrap=px(460))
        self.save_location_label.grid(row=0, column=0, columnspan=2, sticky="ew")
        native.Text(about, "Use only for content you own or have permission to download.",
                    style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, native.PAD))
        row = ttk.Frame(about)
        row.grid(row=2, column=0, sticky="w")
        self.diag_button = native.Button(row, text="Diagnostics…", command=self.open_diagnostics, width=14)
        self.diag_button.pack(side="left")
        self.dev_button = native.Button(row, text="Developer website", width=18,
                                        command=lambda: webbrowser.open("https://github.com/umar-vai"))
        self.dev_button.pack(side="left", padx=(native.GAP, 0))
        dialog.add_button("Close", dialog.close, default=True)

    def _open_settings_dialog(self, tab: str | None = None) -> None:
        window = getattr(self, "settings_window", None)
        try:
            alive = window is not None and window.winfo_exists()
        except Exception:
            alive = False
        if not alive:
            def closed() -> None:
                self.settings_window = None
                self.settings_panel = None

            window = self._build_options_dialog(closed)
            self.settings_window = self.settings_panel = window
        if tab:
            window.select_tab(tab)
        window.show()

    # =========================================================== navigation
    def show_page(self, name: str) -> None:
        """Older code paths ask for 'pages'; in this window they are dialogs or categories."""
        if name == "download":
            self.open_add_url()
        elif name == "batch":
            shell = getattr(self, "native_shell", None)
            if shell is not None:
                shell.batch_visible = True
            else:
                self.batch_dialog.open()
                self.batch_page.focus()
        elif name == "updates":
            shell = getattr(self, "native_shell", None)
            if shell is not None:
                shell.update_visible = True
            else:
                self.about_dialog.open()
        elif name == "settings":
            self.open_settings()
        elif name == "history":
            self._show_category("finished")
        elif name in {"queue", "list"} and self._category in {"finished"}:
            self._show_category("all")
        self._schedule_download_activity_refresh(30)

    def _prefill_add_url(self) -> None:
        if not self.url_var.get().strip():
            try:
                text = self.clipboard_get().strip()
            except Exception:
                text = ""
            if text.startswith(("http://", "https://")) and "\n" not in text and len(text) < 2000:
                self.url_var.set(text)
                self._on_url_changed()

    def open_add_url(self) -> None:
        self._prefill_add_url()
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.add_visible = True
            shell.add_status = self.status_label.cget("text")
            return
        self.add_dialog.open()
        self.after(60, lambda: (self.url_entry.focus_set(), self.url_entry.icursor("end")))

    def _hide_window(self) -> None:
        shell = getattr(self, "native_shell", None)
        if shell is not None and getattr(self, "tray", None) is None:
            shell.show(False)
            return
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
        color = STATUS_COLORS.get(kind, STATUS_COLORS["ready"])
        self.status_label.configure(text=text, foreground=color if kind in {"error", "cancelled"} else "")
        self.statusbar.set_message(text, color)
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.status = text
            if shell.add_visible:
                shell.add_status = text

    # ============================================================ list data
    def _invalidate_history(self) -> None:
        """Reload the history in the background. Reading it checks every file on the disk, and a
        sleeping or busy drive must never freeze the window or the progress of running downloads."""
        self._history_generation += 1
        self._history_built_at = time.monotonic()
        if self._history_loading:
            return  # the running load sees the new generation and loads again when it finishes
        self._history_loading = True
        generation = self._history_generation

        def load() -> None:
            try:
                entries = self.history_store.list()
                rows = [dl.history_row(entry) for entry in entries]
            except Exception:
                entries, rows = [], []
            self.events.put(("history_loaded", {"generation": generation, "entries": entries, "rows": rows}))

        threading.Thread(target=load, daemon=True, name="history-load").start()

    def _history_loaded(self, payload: dict[str, Any]) -> None:
        self._history_loading = False
        if payload.get("generation") != self._history_generation:
            self._history_generation -= 1  # a newer change arrived while loading: load again
            self._invalidate_history()
            return
        entries = payload.get("entries") or []
        self._history_entries = {entry["id"]: entry for entry in entries}
        self._history_rows = list(payload.get("rows") or [])
        self._history_built_at = time.monotonic()
        self._schedule_download_activity_refresh(20)

    def _history_rows_cached(self) -> list[dict[str, Any]]:
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
                    leftover = self._discard_cleanup.pop(item["id"], None)
                    if leftover:
                        self._delete_partials(*leftover)  # the worker has stopped: its files are free
                    removed = True
            if removed:
                snapshots = self.download_queue.snapshot()
        active = self.download_queue.running_count()
        queued = self.download_queue.queued_count()
        try:
            shell = getattr(self, "native_shell", None)
            hidden = not shell.visible if shell is not None else self.state() in {"withdrawn", "iconic"}
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
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.rows_changed = True
            return
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
        self.statusbar.set_totals(totals_summary(totals), totals["speed_text"])
        self._refresh_extension_status(reschedule=False)
        self._update_selection_ui(totals)

    def _refresh_extension_status(self, reschedule: bool = True) -> None:
        """Status-bar pane for the browser link; also polled, since it changes with no download."""
        server = getattr(self, "extension_server", None)
        if server is None:
            ext = "Browser extension: starting…"
        elif server.extension_active():
            ext = "Browser extension: connected"
        else:
            ext = "Browser extension: waiting for browser"
        try:
            if self.statusbar.extension.cget("text") != ext:
                self.statusbar.extension.configure(text=ext)
        except tk.TclError:
            return
        if reschedule:
            self.after(3000, self._refresh_extension_status)

    def _selected_rows(self) -> list[dict[str, Any]]:
        shell = getattr(self, "native_shell", None)
        keys = shell.selected if shell is not None else self.table.selected()
        return [self._rows_by_key[key] for key in keys if key in self._rows_by_key]

    def _update_selection_ui(self, totals: dict[str, Any] | None = None) -> None:
        rows = self._selected_rows()
        totals = totals or dl.totals(self._rows)
        buttons = self.toolbar.buttons
        for key, enabled in toolbar_states(self._rows, rows, totals).items():
            buttons[key].set_enabled(enabled)
        self.details.show(rows[0] if len(rows) == 1 else None, len(rows), dl.selection_summary(rows))

    # ============================================================== actions
    def select_all(self) -> None:
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.select(list(self._shown_keys))
        else:
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
        # One Explorer window per folder (at most a few), not one per selected file.
        seen: set[str] = set()
        for row in self._selected_rows():
            folder = str(Path(row["path"]).parent) if row["path"] else row["save_to"] or str(self.download_dir)
            key = os.path.normcase(folder)
            if key in seen:
                continue
            seen.add(key)
            self._open_folder_row(row)
            if len(seen) >= 5:
                break

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

    def _selected_file_paths(self) -> list[str]:
        """Finished files of the selected rows that are still on the disk (for drag and copy)."""
        return [row["path"] for row in self._selected_rows()
                if row["status"] == "complete" and row["file_exists"] and row["path"] and Path(row["path"]).is_file()]

    def copy_selected_files(self) -> None:
        import file_drag

        paths = self._selected_file_paths()
        if not paths:
            return
        count = file_drag.copy_files(paths)
        if count:
            self._set_status(f"Copied {count} file{'s' if count != 1 else ''}. Paste with Ctrl+V in Telegram, "
                             "WhatsApp or a folder", "ready")
        else:
            self._set_status("Could not copy the files", "error")

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

        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            shell.show_form(native_forms.PropertiesForm(shell, self._row_entry(row), act))
            return
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

    # ------------------------------------------------------- already downloaded
    def _ask_download_again(self, matches: list[dict[str, Any]], new_count: int,
                            choose: Callable[[str], None]) -> None:
        """Ask before fetching finished files again. ``choose`` gets "all" or "new"; nothing is
        called when the user cancels."""
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            shell.show_form(native_forms.DuplicateForm(shell, matches, new_count, choose))
            return
        if len(matches) == 1 and not new_count:
            row = matches[0]
            if messagebox.askyesno(
                    "Already downloaded",
                    f"This file is already downloaded:\n\n{row['name']}\n{Path(row['path']).parent}\n\n"
                    "Do you want to download it again?", parent=self):
                choose("all")
            return
        text = f"{len(matches)} of {len(matches) + new_count} links are already downloaded."
        if new_count:
            answer = messagebox.askyesnocancel(
                "Already downloaded",
                f"{text}\n\nYes: download all of them again\nNo: download only the new ones", parent=self)
            if answer is not None:
                choose("all" if answer else "new")
        elif messagebox.askyesno("Already downloaded", f"{text}\n\nDownload them again?", parent=self):
            choose("all")

    def _queue_links(self, text: str, options: dict[str, str],
                     done: Callable[[str, int, bool], None]) -> None:
        """Queue pasted links, asking first about the ones that are already downloaded.

        ``done(batch_id, queued, again)`` runs once they are in the queue."""
        urls, _duplicates = parse_batch_urls(text)
        mode = str(options.get("mode") or "Video")
        found = {url: dl.find_downloaded(self._history_rows, url, page_url=url, mode=mode,
                                         direct=file_download.looks_like_file(url)) for url in urls}
        matches = [row for row in found.values() if row]
        fresh = [url for url, row in found.items() if not row]

        def queue(choice: str) -> None:
            again = choice == "all" and bool(matches)
            try:
                batch_id, queued, _dupes = self._enqueue_batch_downloads(
                    "\n".join(urls if choice == "all" else fresh), options,
                    **({"extra": {"force_redownload": True}} if again else {}))
            except (ValueError, OSError) as exc:
                messagebox.showerror("Media Downloader", f"Could not add the download.\n\n{exc}", parent=self)
                return
            done(batch_id, queued, again)

        if matches:
            self._ask_download_again(matches, len(fresh), queue)
        else:
            queue("all")

    def cleanup_list(self) -> None:
        """Remove every missing, cancelled and/or failed download in one go."""
        counts: dict[str, int] = {}
        for row in self._rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        if not any(counts.get(status) for status, _text in dl.CLEANUP_STATUSES):
            messagebox.showinfo("Media Downloader", "There are no missing, cancelled or failed downloads.",
                                parent=self)
            return
        picked = CleanupDialog.ask(self, counts)
        # Read the rows again: a download may have been retried while the dialog was open.
        rows = [row for row in self._rows if row["status"] in picked]
        if rows:
            self._delete_rows(rows, False)

    def clean_partial_files(self) -> None:
        """Delete leftover .part/fragment files in every download folder, except those of live downloads."""
        folders: list[str] = [str(self.download_dir)]
        keep: set[Path] = set()
        for row in self._rows:
            folder = str(Path(row["path"]).parent) if row["path"] else str(row["save_to"] or "")
            if folder:
                folders.append(folder)
            if row["source"] == "queue" and row["status"] in dl.STOPPABLE | {"paused", "stopping"}:
                # Unfinished but still in the list: the user may resume it, so its files stay.
                keep.update(path.resolve() for path in find_partial_files(folder, row["name"]))
        files = scan_partial_files(folders, keep)
        if not files:
            messagebox.showinfo("Media Downloader", "No partial files found.", parent=self)
            return
        total = 0
        for path in files:
            total += path_size(path)
        if not messagebox.askyesno(
                "Clean up partial files",
                f"Delete {len(files)} partial file{'s' if len(files) != 1 else ''} ({dl.format_size(total)}) left "
                "by cancelled or failed downloads?\n\nDownloads still in the list that can be resumed are not "
                "touched. This cannot be undone.", parent=self):
            return
        problems: list[str] = []
        for path in files:
            try:
                remove_path(path)
            except OSError as exc:
                problems.append(f"{path.name}: {exc}")
        self._set_status(f"Deleted {len(files) - len(problems)} partial file"
                         f"{'s' if len(files) - len(problems) != 1 else ''}", "ready")
        if problems:
            messagebox.showerror("Media Downloader", "Some files could not be deleted:\n\n" + "\n".join(problems[:12]),
                                 parent=self)

    def _delete_partials(self, folder: str, name: str) -> list[str]:
        """Remove the half-downloaded files (.part, fragments, per-stream pieces) of one download.

        Returns a message for every file that could not be deleted."""
        return delete_partial_files(folder, name)[1]

    def _delete_rows(self, rows: list[dict[str, Any]], delete_files: bool) -> None:
        history_ids: list[str] = []
        problems: list[str] = []
        # Downloads that stay and can still continue: their partial files must survive.
        removed_keys = {row["key"] for row in rows}
        keep = {(os.path.normcase(str(other["save_to"] or "")), other["name"]) for other in self._rows
                if other["key"] not in removed_keys and other["source"] == "queue"
                and other["status"] in dl.STOPPABLE | {"paused", "stopping"}}
        for row in rows:
            request_id = row["request_id"]
            # An unfinished download leaves partial files behind; they go with it automatically.
            partial_dir = ""
            if row["status"] != "complete":
                partial_dir = str(Path(row["path"]).parent) if row["path"] else str(row["save_to"] or "")
                if (os.path.normcase(partial_dir), row["name"]) in keep:
                    partial_dir = ""
            if row["source"] == "queue":
                if row["status"] in dl.STOPPABLE | {"paused", "stopping"}:
                    self._discard_ids.add(request_id)
                    if partial_dir:
                        # The worker may still hold the files: clean up once it has stopped.
                        self._discard_cleanup[request_id] = (partial_dir, row["name"])
                    self.cancel_download(request_id)
                else:
                    self.download_queue.remove(request_id)
                    if partial_dir:
                        problems += self._delete_partials(partial_dir, row["name"])
            else:
                if partial_dir:
                    problems += self._delete_partials(partial_dir, row["name"])
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

            counts = dl.action_counts(rows) if len(rows) > 1 else {}

            def add(label: str, command: Any, key: str | None = None, accelerator: str = "") -> None:
                # With several rows selected, say how many each command will act on.
                if key is not None and counts.get(key, 0) > 1:
                    label = f"{label} ({counts[key]})"
                menu.add_command(label=label, command=command, accelerator=accelerator,
                                 state="normal" if key is None or key in actions else "disabled")

            if len(rows) > 1:
                menu.add_command(label=f"{len(rows)} items selected", state="disabled")
                menu.add_separator()
            add("Open", self.open_selected, "open", "Enter")
            add("Open folder", self.open_selected_folder, "open_folder")
            add("Open in media editor", self.edit_selected, "edit")
            menu.add_separator()
            add("Resume", self.resume_selected, "resume")
            add("Stop", self.stop_selected, "stop")
            add("Cancel download", self.cancel_selected, "cancel")
            add("Download again", self.redownload_selected, "redownload")
            menu.add_separator()
            add("Copy file", self.copy_selected_files, "copy_file", "Ctrl+Shift+C")
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
        if server is None:
            status = "Browser link is starting…"
        elif server.extension_active():
            status = f"Extension connected (port {server.port})"
        else:
            status = f"Listening on port {server.port}; waiting for the extension"
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
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.add_visible = False
        self.clear_form()
        self._set_status(f"Added to downloads: {name}", "ready")
        if self._category not in {"all", "unfinished", "queue:active", dl.category_for(name, self.mode_var.get())}:
            self._show_category("all")
        self._render_download_activity()
        if shell is not None:
            shell.select([f"q:{request_id}"])
        else:
            self.table.select([f"q:{request_id}"])

    def _after_batch_queued(self, batch_id: str) -> None:
        self.batch_dialog.close()
        self._render_download_activity()
        key = f"queue:{batch_id}"
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.batch_visible = False
            self._select_category(key)
        else:
            self._show_category(key if self.category_tree.tree.exists(key) else "all")

    # ============================================================ thumbnail
    def _reset_thumbnail_label(self) -> None:
        self.thumbnail_image = None
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.add_thumbnail = ""
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
            shell = getattr(self, "native_shell", None)
            if shell is not None:
                from base64 import b64encode

                png = io.BytesIO()
                canvas.save(png, format="PNG")
                shell.add_thumbnail = b64encode(png.getvalue()).decode("ascii")
                return
            photo = ImageTk.PhotoImage(canvas)
            self.thumbnail_image = photo
            self.thumb.set_image(photo)
        except Exception:
            self._reset_thumbnail_label()


class _Thumb:
    """The preview picture in Add URL."""

    def __init__(self, frame: tk.Frame) -> None:
        self.label = tk.Label(frame, text="No preview", bg=C["SURFACE_3"], fg=C["FAINT"], font=native.font(frame))
        self.label.pack(fill="both", expand=True)

    def set_image(self, photo: Any) -> None:
        self.label.configure(image=photo or "", text="" if photo else "No preview")


class _Combo(ttk.Combobox):
    """Read-only combo box; ``state="normal"`` from older code means "enabled" (read-only)."""

    def __init__(self, master: tk.Misc, var: tk.Variable, values: list[str], width: int = 16) -> None:
        super().__init__(master, textvariable=var, values=values, state="readonly", width=width)

    def configure(self, cnf: Any = None, **kw: Any) -> Any:
        if kw.get("state") == "normal":
            kw["state"] = "readonly"
        if cnf is None and not kw:
            return super().configure()
        return super().configure(cnf, **kw)

    config = configure
