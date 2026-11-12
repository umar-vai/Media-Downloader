from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk
from tkinter import filedialog, messagebox

from install_mode import install_mode_name
from version import APP_VERSION

BG = "#060B14"
SURFACE = "#0B1323"
SURFACE_2 = "#101C31"
SURFACE_3 = "#14223A"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
CYAN = "#23D5FF"
PURPLE = "#7657FF"
PURPLE_HOVER = "#6547E9"
SUCCESS = "#24D18C"


class SettingsWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master: ctk.CTkBaseClass,
        settings: dict[str, Any],
        *,
        on_save: Callable[[dict[str, Any]], None],
        on_check_updates: Callable[[], None],
        on_open_diagnostics: Callable[[], None],
        on_open_log: Callable[[], None],
        on_open_release: Callable[[], None],
        on_export_extension: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Media Downloader Settings")
        self.geometry("760x650")
        self.minsize(700, 570)
        self.configure(fg_color=BG)
        self.transient(master)

        self._original = copy.deepcopy(settings)
        self._on_save = on_save
        self._on_check_updates = on_check_updates
        self._on_open_diagnostics = on_open_diagnostics
        self._on_open_log = on_open_log
        self._on_open_release = on_open_release
        self._on_export_extension = on_export_extension
        self._dirty = False

        self.download_dir_var = ctk.StringVar(value=str(settings.get("download_dir") or ""))
        self.default_mode_var = ctk.StringVar(value=str(settings.get("default_mode") or "Video"))
        self.video_quality_var = ctk.StringVar(value=str(settings.get("video_quality") or "720p"))
        self.audio_format_var = ctk.StringVar(value=str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var = ctk.StringVar(value=str(settings.get("audio_quality") or "192"))
        self.max_concurrent_var = ctk.StringVar(value=str(settings.get("max_concurrent_downloads", 6)))
        self.auto_analyze_var = ctk.BooleanVar(value=bool(settings.get("auto_analyze_links", True)))
        self.open_editor_after_var = ctk.BooleanVar(value=bool(settings.get("open_editor_after_download", False)))
        self.confirm_exit_var = ctk.BooleanVar(value=bool(settings.get("confirm_before_exit", True)))
        self.close_to_tray_var = ctk.BooleanVar(value=bool(settings.get("close_to_tray", True)))
        self.auto_check_updates_var = ctk.BooleanVar(value=bool(settings.get("auto_check_updates", True)))
        self.auto_download_updates_var = ctk.BooleanVar(value=bool(settings.get("auto_download_updates", False)))

        # advanced options: key -> tk variable (saved as-is)
        self._extra_vars: dict[str, Any] = {
            "skip_existing": ctk.BooleanVar(value=bool(settings.get("skip_existing", True))),
            "organize_by_site": ctk.BooleanVar(value=bool(settings.get("organize_by_site", False))),
            "fast_mode": ctk.BooleanVar(value=bool(settings.get("fast_mode", True))),
            "embed_metadata": ctk.BooleanVar(value=bool(settings.get("embed_metadata", True))),
            "embed_thumbnail": ctk.BooleanVar(value=bool(settings.get("embed_thumbnail", False))),
            "subtitles": ctk.StringVar(value=str(settings.get("subtitles") or "Off")),
            "subtitle_langs": ctk.StringVar(value=str(settings.get("subtitle_langs") or "en")),
            "clipboard_hotkey": ctk.BooleanVar(value=bool(settings.get("clipboard_hotkey", True))),
            "extension_enabled": ctk.BooleanVar(value=bool(settings.get("extension_enabled", True))),
            "cookies_file": ctk.StringVar(value=str(settings.get("cookies_file") or "")),
            "cookies_browser": ctk.StringVar(value=str(settings.get("cookies_browser") or "None")),
        }
        self._folder_rules_initial = str(settings.get("folder_rules") or "")
        self._token = str(settings.get("extension_token") or "")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_tabs()
        self._build_footer()
        self.protocol("WM_DELETE_WINDOW", self._close)

        for var in (
            self.download_dir_var,
            self.default_mode_var,
            self.video_quality_var,
            self.audio_format_var,
            self.audio_quality_var,
            self.max_concurrent_var,
            self.auto_analyze_var,
            self.open_editor_after_var,
            self.confirm_exit_var,
            self.close_to_tray_var,
            self.auto_check_updates_var,
            self.auto_download_updates_var,
            *self._extra_vars.values(),
        ):
            var.trace_add("write", lambda *_args: self._mark_dirty())

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="SETTINGS",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            header,
            text="Configure downloads, app behavior, updates and support tools.",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

        self.state_label = ctk.CTkLabel(
            header,
            text="SAVED",
            width=72,
            height=28,
            corner_radius=8,
            fg_color="#0E3025",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 9),
        )
        self.state_label.grid(row=0, column=1, rowspan=2, padx=20)

    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=SURFACE,
            segmented_button_fg_color=SURFACE_2,
            segmented_button_selected_color=PURPLE,
            segmented_button_selected_hover_color=PURPLE_HOVER,
            segmented_button_unselected_color=SURFACE_2,
            segmented_button_unselected_hover_color=SURFACE_3,
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=18, pady=16)
        for name in ("General", "Downloads", "Files & rules", "Browser", "Updates", "About"):
            self.tabs.add(name)

        self._build_general_tab(self.tabs.tab("General"))
        self._build_downloads_tab(self.tabs.tab("Downloads"))
        self._build_files_tab(self.tabs.tab("Files & rules"))
        self._build_browser_tab(self.tabs.tab("Browser"))
        self._build_updates_tab(self.tabs.tab("Updates"))
        self._build_about_tab(self.tabs.tab("About"))

    def _section_label(self, parent: ctk.CTkBaseClass, text: str, row: int) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            text_color=CYAN,
            font=("Segoe UI Semibold", 9),
            anchor="w",
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 7))

    def _switch(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        text: str,
        variable: ctk.BooleanVar,
        detail: str,
    ) -> None:
        block = ctk.CTkFrame(parent, fg_color=SURFACE_2, corner_radius=10)
        block.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        block.grid_columnconfigure(0, weight=1)
        ctk.CTkSwitch(
            block,
            text=text,
            variable=variable,
            text_color=TEXT,
            progress_color=PURPLE,
            button_color=TEXT,
            button_hover_color=CYAN,
            font=("Segoe UI", 11),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            block,
            text=detail,
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=580,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

    def _build_general_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        self._section_label(tab, "APP BEHAVIOR", 0)
        self._switch(
            tab,
            1,
            "Automatically analyze valid links",
            self.auto_analyze_var,
            "After a valid public http:// or https:// media URL is pasted or typed, try to load its title, creator and duration automatically.",
        )
        self._switch(
            tab,
            2,
            "Open downloaded media in the editor",
            self.open_editor_after_var,
            "Use the editor automatically after a successful download. You can still override this from the main screen.",
        )
        self._switch(
            tab,
            3,
            "Confirm before closing active tasks",
            self.confirm_exit_var,
            "Ask before closing the app while a download, analysis or update is still running.",
        )
        self._switch(
            tab,
            4,
            "Keep running in the system tray when closed",
            self.close_to_tray_var,
            "The X button hides the window to the tray so downloads continue. Use the tray icon's Quit to exit.",
        )

    def _build_downloads_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        self._section_label(tab, "SAVE LOCATION", 0)

        folder = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        folder.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        folder.grid_columnconfigure(0, weight=1)
        self.folder_entry = ctk.CTkEntry(
            folder,
            textvariable=self.download_dir_var,
            height=36,
            fg_color=SURFACE_3,
            border_color=BORDER,
        )
        self.folder_entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        ctk.CTkButton(
            folder,
            text="Browse",
            width=82,
            height=36,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self._browse_download_folder,
        ).grid(row=0, column=1, padx=(0, 12), pady=12)

        self._section_label(tab, "DEFAULT DOWNLOAD FORMAT", 2)

        left = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        left.grid(row=3, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Default media type", text_color=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkOptionMenu(
            left,
            variable=self.default_mode_var,
            values=["Video", "Audio"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        right = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        right.grid(row=3, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text="Default video quality", text_color=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkOptionMenu(
            right,
            variable=self.video_quality_var,
            values=["Best available", "1080p", "720p", "480p", "360p"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        audio_left = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        audio_left.grid(row=4, column=0, sticky="nsew", padx=(10, 5), pady=5)
        audio_left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(audio_left, text="Default audio format", text_color=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkOptionMenu(
            audio_left,
            variable=self.audio_format_var,
            values=["MP3", "M4A", "FLAC", "OPUS", "WAV"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        audio_right = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        audio_right.grid(row=4, column=1, sticky="nsew", padx=(5, 10), pady=5)
        audio_right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(audio_right, text="Default MP3 quality", text_color=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkOptionMenu(
            audio_right,
            variable=self.audio_quality_var,
            values=["320", "256", "192", "128"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        self._section_label(tab, "BATCH DOWNLOAD PERFORMANCE", 5)
        parallel = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        parallel.grid(row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        parallel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            parallel,
            text="Simultaneous downloads (1–10)",
            text_color=TEXT,
            font=("Segoe UI", 11),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        ctk.CTkOptionMenu(
            parallel,
            variable=self.max_concurrent_var,
            values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            width=110,
            fg_color=SURFACE_3,
            button_color=PURPLE,
        ).grid(row=0, column=1, sticky="e", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            parallel,
            text="Default: 6. Higher numbers can cause site rate limits or slower individual downloads.",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            wraplength=580,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))

    def _scroll_area(self, tab: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        area = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        area.grid(row=0, column=0, sticky="nsew")
        area.grid_columnconfigure(0, weight=1)
        return area

    def _build_files_tab(self, tab: ctk.CTkFrame) -> None:
        area = self._scroll_area(tab)
        self._section_label(area, "FILES", 0)
        self._switch(area, 1, "Skip files that are already downloaded",
                     self._extra_vars["skip_existing"],
                     "If a finished file with the same name already exists in the folder, the link is marked done instead of downloading again.")
        self._switch(area, 2, "Organize into a folder per website",
                     self._extra_vars["organize_by_site"],
                     "Saves into sub-folders such as YouTube or Facebook. Folder rules below take priority.")
        self._switch(area, 3, "Fast mode (16 parallel fragments)",
                     self._extra_vars["fast_mode"],
                     "Downloads HLS/DASH streams with more parallel connections. Turn off if a site rate-limits you.")
        self._switch(area, 4, "Embed metadata (title, chapters)",
                     self._extra_vars["embed_metadata"],
                     "Writes the title, creator and chapters into the file.")
        self._switch(area, 5, "Embed thumbnail as cover art",
                     self._extra_vars["embed_thumbnail"],
                     "Adds the video thumbnail to the file. Slightly slower; not supported for WAV.")

        self._section_label(area, "SUBTITLES (VIDEO)", 6)
        sub = ctk.CTkFrame(area, fg_color=SURFACE_2, corner_radius=10)
        sub.grid(row=7, column=0, sticky="ew", padx=10, pady=5)
        sub.grid_columnconfigure(1, weight=1)
        ctk.CTkOptionMenu(sub, variable=self._extra_vars["subtitles"],
                          values=["Off", "Sidecar file", "Embed in video"], fg_color=SURFACE_3,
                          button_color=PURPLE, width=150).grid(row=0, column=0, padx=12, pady=12)
        ctk.CTkEntry(sub, textvariable=self._extra_vars["subtitle_langs"], height=34, fg_color=SURFACE_3,
                     border_color=BORDER, placeholder_text="Languages, e.g. en,bn").grid(
            row=0, column=1, sticky="ew", padx=(0, 12), pady=12)

        self._section_label(area, "FOLDER & QUALITY RULES", 8)
        rules = ctk.CTkFrame(area, fg_color=SURFACE_2, corner_radius=10)
        rules.grid(row=9, column=0, sticky="ew", padx=10, pady=5)
        rules.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            rules,
            text="One rule per line:  website => folder | quality\nExample:  example.com => D:\\Videos\\Example | 1080p",
            text_color=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        self.rules_box = ctk.CTkTextbox(rules, height=110, fg_color=SURFACE_3, border_color=BORDER,
                                        border_width=1, font=("Consolas", 11))
        self.rules_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.rules_box.insert("1.0", self._folder_rules_initial)
        self.rules_box.bind("<KeyRelease>", lambda _e: self._mark_dirty())

        self._section_label(area, "SHORTCUTS", 10)
        self._switch(area, 11, "Global hotkeys: Ctrl+Alt+D (video) and Ctrl+Alt+M (audio)",
                     self._extra_vars["clipboard_hotkey"],
                     "Copy a link anywhere, press the hotkey, and it is added to the queue, even while the app is in the tray. Ctrl+K opens the command palette inside the app.")

    def _build_browser_tab(self, tab: ctk.CTkFrame) -> None:
        area = self._scroll_area(tab)
        self._section_label(area, "COOKIES FOR LOGGED-IN SITES", 0)
        box = ctk.CTkFrame(area, fg_color=SURFACE_2, corner_radius=10)
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="cookies.txt file (Netscape format)", text_color=MUTED,
                     font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkEntry(box, textvariable=self._extra_vars["cookies_file"], height=34, fg_color=SURFACE_3,
                     border_color=BORDER).grid(row=1, column=0, sticky="ew", padx=(12, 8), pady=(0, 10))
        ctk.CTkButton(box, text="Browse", width=82, height=34, fg_color=PURPLE, hover_color=PURPLE_HOVER,
                      command=self._browse_cookies).grid(row=1, column=1, padx=(0, 12), pady=(0, 10))
        ctk.CTkLabel(box, text="...or read cookies straight from an installed browser", text_color=MUTED,
                     font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 4))
        ctk.CTkOptionMenu(box, variable=self._extra_vars["cookies_browser"],
                          values=["None", "Firefox", "Chrome", "Edge", "Brave"], fg_color=SURFACE_3,
                          button_color=PURPLE, width=150).grid(row=3, column=0, sticky="w", padx=12, pady=(0, 12))
        ctk.CTkLabel(
            area,
            text="Cookies are only sent to the sites you download from. Chrome/Edge may block reading their cookies while open; Firefox or a cookies.txt export is the most reliable.",
            text_color=MUTED, font=("Segoe UI", 9), wraplength=580, justify="left", anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 6))

        self._section_label(area, "BROWSER EXTENSION", 3)
        self._switch(area, 4, "Accept links from the browser extension",
                     self._extra_vars["extension_enabled"],
                     "Runs a local-only connection (127.0.0.1) protected by the pairing token below. Restart the app after changing this.")
        ext = ctk.CTkFrame(area, fg_color=SURFACE_2, corner_radius=10)
        ext.grid(row=5, column=0, sticky="ew", padx=10, pady=5)
        ext.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ext, text="Pairing token", text_color=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        token_entry = ctk.CTkEntry(ext, height=34, fg_color=SURFACE_3, border_color=BORDER)
        token_entry.insert(0, self._token)
        token_entry.configure(state="readonly")
        token_entry.grid(row=1, column=0, sticky="ew", padx=(12, 8), pady=(0, 10))
        ctk.CTkButton(ext, text="Copy", width=70, height=34, fg_color=PURPLE, hover_color=PURPLE_HOVER,
                      command=self._copy_token).grid(row=1, column=1, padx=(0, 12), pady=(0, 10))
        ctk.CTkButton(ext, text="Export extension files...", height=36, fg_color=SURFACE_3, hover_color="#1B3153",
                      border_width=1, border_color=BORDER, command=self._export_extension).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        ctk.CTkLabel(
            ext,
            text="1) Export the files.  2) Chrome/Edge/Brave: open the extensions page, enable Developer mode, choose Load unpacked and pick the folder. Firefox: about:debugging, Load Temporary Add-on, pick manifest.json.  3) Open the extension options and paste the token.",
            text_color=MUTED, font=("Segoe UI", 9), wraplength=560, justify="left", anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))

    def _browse_cookies(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose cookies.txt", parent=self,
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")],
        )
        if selected:
            self._extra_vars["cookies_file"].set(selected)

    def _copy_token(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._token)
        self.status_label.configure(text="Pairing token copied.")

    def _export_extension(self) -> None:
        if self._on_export_extension:
            self._on_export_extension()

    def _build_updates_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        self._section_label(tab, "AUTOMATIC UPDATES", 0)
        self._switch(
            tab,
            1,
            "Automatically check for updates",
            self.auto_check_updates_var,
            "Check GitHub Releases shortly after Media Downloader starts.",
        )
        self._switch(
            tab,
            2,
            "Automatically download verified updates",
            self.auto_download_updates_var,
            "Download an available update after SHA-256 verification. Installation still uses the safe updater flow.",
        )

        actions = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=(10, 5))
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            actions,
            text="Check for updates now",
            height=38,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self._check_updates,
        ).grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=12)
        ctk.CTkButton(
            actions,
            text="Open latest release",
            height=38,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            border_width=1,
            border_color=BORDER,
            command=self._on_open_release,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 12), pady=12)

    def _build_about_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        mode = install_mode_name().capitalize()
        ctk.CTkLabel(
            tab,
            text="Media Downloader",
            text_color=TEXT,
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(20, 2))
        ctk.CTkLabel(
            tab,
            text=f"Version {APP_VERSION} • {mode} mode",
            text_color=CYAN,
            font=("Segoe UI Semibold", 10),
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 16))

        ctk.CTkLabel(
            tab,
            text="Support tools",
            text_color=MUTED,
            font=("Segoe UI", 10),
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 6))

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=10)
        for column in range(2):
            actions.grid_columnconfigure(column, weight=1)

        ctk.CTkButton(
            actions,
            text="System diagnostics",
            height=40,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self._on_open_diagnostics,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=5)
        ctk.CTkButton(
            actions,
            text="Open application log",
            height=40,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self._on_open_log,
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=5)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            footer,
            text="Changes are saved only when you press Save settings.",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=18)

        ctk.CTkButton(
            footer,
            text="Cancel",
            width=92,
            height=38,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            command=self._close,
        ).grid(row=0, column=1, padx=(6, 6), pady=14)

        self.save_button = ctk.CTkButton(
            footer,
            text="Save settings",
            width=120,
            height=38,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self._save,
            state="disabled",
        )
        self.save_button.grid(row=0, column=2, padx=(6, 18), pady=14)

    def _mark_dirty(self) -> None:
        if self._dirty:
            return
        self._dirty = True
        self.state_label.configure(text="UNSAVED", fg_color="#2D2514", text_color="#FFB84D")
        self.save_button.configure(state="normal")

    def _browse_download_folder(self) -> None:
        current = Path(self.download_dir_var.get()).expanduser()
        selected = filedialog.askdirectory(
            title="Choose download folder",
            initialdir=str(current if current.exists() else Path.home()),
            mustexist=True,
            parent=self,
        )
        if selected:
            self.download_dir_var.set(selected)

    def _payload(self) -> dict[str, Any]:
        payload = copy.deepcopy(self._original)
        payload.update(
            {
                "download_dir": str(Path(self.download_dir_var.get().strip()).expanduser()),
                "default_mode": self.default_mode_var.get(),
                "video_quality": self.video_quality_var.get(),
                "audio_format": self.audio_format_var.get(),
                "audio_quality": self.audio_quality_var.get(),
                "max_concurrent_downloads": int(self.max_concurrent_var.get()),
                "auto_analyze_links": bool(self.auto_analyze_var.get()),
                "open_editor_after_download": bool(self.open_editor_after_var.get()),
                "confirm_before_exit": bool(self.confirm_exit_var.get()),
                "close_to_tray": bool(self.close_to_tray_var.get()),
                "auto_check_updates": bool(self.auto_check_updates_var.get()),
                "auto_download_updates": bool(self.auto_download_updates_var.get()),
                "folder_rules": self.rules_box.get("1.0", "end-1c").strip(),
            }
        )
        for key, var in self._extra_vars.items():
            payload[key] = var.get()
        return payload

    def _save(self) -> bool:
        payload = self._payload()
        try:
            self._on_save(payload)
        except Exception as exc:
            self.state_label.configure(text="ERROR", fg_color="#351722", text_color="#FF647C")
            self.status_label.configure(text="Settings were not saved.")
            messagebox.showerror(
                "Media Downloader Settings",
                str(exc) or "Could not save settings.",
                parent=self,
            )
            return False

        self._original = copy.deepcopy(payload)
        self._dirty = False
        self.state_label.configure(text="SAVED", fg_color="#0E3025", text_color=SUCCESS)
        self.save_button.configure(state="disabled")
        self.status_label.configure(text="Settings saved and applied.")
        self.after(1800, lambda: self.status_label.configure(text="Changes are saved only when you press Save settings.") if self.winfo_exists() else None)
        return True

    def _check_updates(self) -> None:
        if self._dirty and not self._save():
            return
        self._on_check_updates()

    def _close(self) -> None:
        if self._dirty:
            if not messagebox.askyesno(
                "Media Downloader Settings",
                "Discard unsaved settings changes?",
                parent=self,
            ):
                return
        self.destroy()
