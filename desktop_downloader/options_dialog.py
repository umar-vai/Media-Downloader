"""Options: a classic Windows property sheet (tabs, group boxes, OK / Cancel / Apply).

Every setting the app has is here; ``on_save`` receives the full settings dict, exactly like
before, and raises on errors (shown in a message box, the dialog stays open).
"""

from __future__ import annotations

import copy
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import native
from download_rules import QUALITIES, parse_rules
from install_mode import install_mode_name
from native import GAP, PAD, check, combo, group
from theme import THEMES
from version import APP_VERSION

SITE_QUALITIES = ["Default", "Best available", "1080p", "720p", "480p", "360p"]
TABS = ("General", "Downloads", "Save To", "Files", "Browser", "Updates")
_TAB_ALIASES = {"Website folders": "Save To", "About": "Updates"}


def rules_from_rows(rows: list[tuple[str, str, str]]) -> str:
    """Serialize the Save To table to the ``folder_rules`` text format."""
    lines: list[str] = []
    for host, folder, quality in rows:
        host = str(host or "").strip()
        folder = str(folder or "").strip()
        quality = str(quality or "").strip()
        if quality not in QUALITIES:
            quality = ""
        if not host or (not folder and not quality):
            continue
        line = host
        if folder:
            line += f" => {folder}"
        if quality:
            line += f" | {quality}"
        lines.append(line)
    return "\n".join(lines)


class SiteRuleDialog(native.Dialog):
    """Add / edit one website folder rule."""

    def __init__(self, master: tk.Misc, host: str = "", folder: str = "", quality: str = "Default") -> None:
        super().__init__(master, "Website folder", modal=True)
        self.result: tuple[str, str, str] | None = None
        self.host = tk.StringVar(value=host)
        self.folder = tk.StringVar(value=folder)
        self.quality = tk.StringVar(value=quality if quality in SITE_QUALITIES else "Default")
        body = self.body
        ttk.Label(body, text="Downloads from this website are saved in their own folder.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, PAD))
        ttk.Label(body, text="Website:").grid(row=1, column=0, sticky="w", pady=GAP)
        entry = ttk.Entry(body, textvariable=self.host, width=44)
        entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=GAP)
        ttk.Label(body, text="e.g. youtube.com (also covers m.youtube.com)", style="Hint.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w")
        ttk.Label(body, text="Folder:").grid(row=3, column=0, sticky="w", pady=GAP)
        ttk.Entry(body, textvariable=self.folder, width=36).grid(row=3, column=1, sticky="ew", pady=GAP)
        ttk.Button(body, text="Browse…", command=self._browse).grid(row=3, column=2, padx=(GAP, 0))
        ttk.Label(body, text="Quality:").grid(row=4, column=0, sticky="w", pady=GAP)
        combo(body, self.quality, SITE_QUALITIES, width=18).grid(row=4, column=1, sticky="w", pady=GAP)
        body.grid_columnconfigure(1, weight=1)
        self.add_button("Cancel", self.cancel)
        self.add_button("OK", self._ok, default=True)
        self.show()
        entry.focus_set()

    def _browse(self) -> None:
        current = Path(self.folder.get()).expanduser()
        chosen = filedialog.askdirectory(parent=self, title="Choose the folder for this website",
                                         initialdir=str(current if current.exists() else Path.home()))
        if chosen:
            self.folder.set(chosen)

    def _ok(self) -> None:
        host = self.host.get().strip()
        if not host:
            messagebox.showerror("Website folder", "Enter the website, for example youtube.com.", parent=self)
            return
        if not self.folder.get().strip() and self.quality.get() == "Default":
            messagebox.showerror("Website folder", "Choose a folder or a quality for this website.", parent=self)
            return
        self.result = (host, self.folder.get().strip(), self.quality.get())
        self.close()


class OptionsDialog(native.Dialog):
    def __init__(
        self,
        master: tk.Misc,
        settings: dict[str, Any],
        *,
        on_save: Callable[[dict[str, Any]], None],
        on_check_updates: Callable[[], None],
        on_open_diagnostics: Callable[[], None],
        on_open_log: Callable[[], None],
        on_open_release: Callable[[], None],
        on_export_extension: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        extension_status: str = "",
        icon: str | None = None,
    ) -> None:
        super().__init__(master, "Options", icon=icon)
        self._original = copy.deepcopy(settings)
        self._on_save = on_save
        self._on_close = on_close
        self._on_check_updates = on_check_updates
        self._on_open_diagnostics = on_open_diagnostics
        self._on_open_log = on_open_log
        self._on_open_release = on_open_release
        self._on_export_extension = on_export_extension
        self._token = str(settings.get("extension_token") or "")
        self._dirty = False

        def boolean(key: str, default: bool) -> tk.BooleanVar:
            return tk.BooleanVar(self, value=bool(settings.get(key, default)))

        def string(key: str, default: str) -> tk.StringVar:
            return tk.StringVar(self, value=str(settings.get(key) or default))

        theme_name = str(settings.get("theme") or "Light")
        self.vars: dict[str, tk.Variable] = {
            "download_dir": string("download_dir", ""),
            "default_mode": string("default_mode", "Video"),
            "video_quality": string("video_quality", "720p"),
            "audio_format": string("audio_format", "MP3"),
            "audio_quality": string("audio_quality", "192"),
            "max_concurrent_downloads": tk.StringVar(self, value=str(settings.get("max_concurrent_downloads", 6))),
            "auto_analyze_links": boolean("auto_analyze_links", True),
            "open_editor_after_download": boolean("open_editor_after_download", False),
            "confirm_before_exit": boolean("confirm_before_exit", True),
            "close_to_tray": boolean("close_to_tray", True),
            "start_with_windows": boolean("start_with_windows", False),
            "auto_check_updates": boolean("auto_check_updates", True),
            "auto_download_updates": boolean("auto_download_updates", False),
            "theme": tk.StringVar(self, value=theme_name if theme_name in THEMES else "Light"),
            "skip_existing": boolean("skip_existing", True),
            "organize_by_site": boolean("organize_by_site", False),
            "fast_mode": boolean("fast_mode", True),
            "embed_metadata": boolean("embed_metadata", True),
            "embed_thumbnail": boolean("embed_thumbnail", False),
            "subtitles": string("subtitles", "Off"),
            "subtitle_langs": string("subtitle_langs", "en"),
            "clipboard_hotkey": boolean("clipboard_hotkey", True),
            "extension_enabled": boolean("extension_enabled", True),
            "cookies_file": string("cookies_file", ""),
            "cookies_browser": string("cookies_browser", "None"),
        }

        self.tabs = ttk.Notebook(self.body)
        self.tabs.pack(fill="both", expand=True)
        self._pages: dict[str, ttk.Frame] = {}
        for name in TABS:
            page = ttk.Frame(self.tabs, padding=(PAD, PAD, PAD, PAD))
            page.grid_columnconfigure(0, weight=1)
            self.tabs.add(page, text=f"  {name}  ")
            self._pages[name] = page
        self._build_general(self._pages["General"])
        self._build_downloads(self._pages["Downloads"])
        self._build_save_to(self._pages["Save To"], str(settings.get("folder_rules") or ""))
        self._build_files(self._pages["Files"])
        self._build_browser(self._pages["Browser"], extension_status)
        self._build_updates(self._pages["Updates"])

        self.apply_button = self.add_button("Apply", self.apply)
        self.add_button("Cancel", self.cancel)
        self.add_button("OK", self.ok, default=True)
        self.apply_button.configure(state="disabled")
        for var in self.vars.values():
            var.trace_add("write", lambda *_a: self._mark_dirty())
        self.show()

    # ------------------------------------------------------------------ tabs
    def _build_general(self, tab: ttk.Frame) -> None:
        v = self.vars
        box = group(tab, "Startup and exit", row=0, column=0, sticky="ew")
        check(box, "Start Media Downloader when Windows starts", v["start_with_windows"], 0)
        check(box, "Keep running in the notification area when the window is closed", v["close_to_tray"], 1)
        check(box, "Ask before closing while downloads are running", v["confirm_before_exit"], 2)

        box = group(tab, "Links", row=1, column=0, sticky="ew", pady=(PAD, 0))
        check(box, "Read a link's title and formats as soon as it is pasted", v["auto_analyze_links"], 0)
        check(box, "Open finished downloads in the media editor", v["open_editor_after_download"], 1)
        check(box, "Global hotkeys: Ctrl+Alt+D downloads the copied link, Ctrl+Alt+M its audio",
              v["clipboard_hotkey"], 2)

        box = group(tab, "Appearance", row=2, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Label(box, text="Theme:").grid(row=0, column=0, sticky="w", padx=(0, GAP))
        combo(box, v["theme"], list(THEMES), width=12).grid(row=0, column=1, sticky="w")
        ttk.Label(box, text="Used the next time the app starts.", style="Hint.TLabel").grid(
            row=0, column=2, sticky="w", padx=(PAD, 0))

    def _build_downloads(self, tab: ttk.Frame) -> None:
        v = self.vars
        box = group(tab, "Default download folder", row=0, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=v["download_dir"], width=50).grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text="Browse…", command=self._browse_download_folder).grid(row=0, column=1, padx=(GAP, 0))

        box = group(tab, "Default format", row=1, column=0, sticky="ew", pady=(PAD, 0))
        rows = (("Download type:", "default_mode", ["Video", "Audio"]),
                ("Video quality:", "video_quality", ["Best available", "1080p", "720p", "480p", "360p"]),
                ("Audio format:", "audio_format", ["MP3", "M4A", "FLAC", "OPUS", "WAV"]),
                ("Audio bitrate (kbps):", "audio_quality", ["320", "256", "192", "128"]))
        for row, (label, key, values) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, PAD))
            combo(box, v[key], values, width=16).grid(row=row, column=1, sticky="w", pady=2)

        box = group(tab, "Connections", row=2, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Label(box, text="Maximum simultaneous downloads:").grid(row=0, column=0, sticky="w", padx=(0, PAD))
        ttk.Spinbox(box, from_=1, to=10, textvariable=v["max_concurrent_downloads"], width=5,
                    state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(box, text="Default 6. More can trigger rate limits on some sites.", style="Hint.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_save_to(self, tab: ttk.Frame, rules_text: str) -> None:
        tab.grid_rowconfigure(1, weight=1)
        ttk.Label(tab, text="Downloads from these websites are saved in their own folder (and quality):",
                  wraplength=480).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, GAP))
        self.sites = ttk.Treeview(tab, columns=("host", "folder", "quality"), show="headings", height=8,
                                  selectmode="browse")
        for key, title, width in (("host", "Website", 130), ("folder", "Folder", 250), ("quality", "Quality", 90)):
            self.sites.heading(key, text=title, anchor="w")
            self.sites.column(key, width=width, anchor="w", stretch=key == "folder")
        self.sites.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.sites.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.sites.configure(yscrollcommand=scroll.set)
        side = ttk.Frame(tab)
        side.grid(row=1, column=2, sticky="n", padx=(PAD, 0))
        ttk.Button(side, text="Add…", command=self._add_site, width=11).pack(pady=(0, GAP))
        ttk.Button(side, text="Edit…", command=self._edit_site, width=11).pack(pady=(0, GAP))
        ttk.Button(side, text="Remove", command=self._remove_site, width=11).pack()
        self.sites.bind("<Double-1>", lambda _e: self._edit_site())
        self.sites.bind("<Delete>", lambda _e: self._remove_site())
        for rule in parse_rules(rules_text):
            self.sites.insert("", "end", values=(rule.host, rule.folder, rule.quality or "Default"))
        check(tab, "Put other downloads in a sub-folder named after the website", self.vars["organize_by_site"],
              2, columnspan=3, pady=(PAD, 0))

    def _build_files(self, tab: ttk.Frame) -> None:
        v = self.vars
        box = group(tab, "Saving", row=0, column=0, sticky="ew")
        check(box, "Skip files that are already in the folder", v["skip_existing"], 0)
        check(box, "Fast mode: download stream pieces in parallel", v["fast_mode"], 1)
        box = group(tab, "Metadata", row=1, column=0, sticky="ew", pady=(PAD, 0))
        check(box, "Write the title, artist and chapters into the file", v["embed_metadata"], 0)
        check(box, "Add the video thumbnail as cover art", v["embed_thumbnail"], 1)
        box = group(tab, "Subtitles (video)", row=2, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Label(box, text="Subtitles:").grid(row=0, column=0, sticky="w", padx=(0, PAD), pady=2)
        combo(box, v["subtitles"], ["Off", "Sidecar file", "Embed in video"], width=16).grid(
            row=0, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Languages:").grid(row=1, column=0, sticky="w", padx=(0, PAD), pady=2)
        ttk.Entry(box, textvariable=v["subtitle_langs"], width=20).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Comma separated, e.g. en,bn", style="Hint.TLabel").grid(
            row=1, column=2, sticky="w", padx=(PAD, 0))

    def _build_browser(self, tab: ttk.Frame, status: str) -> None:
        v = self.vars
        box = group(tab, "Browser extension", row=0, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        check(box, "Catch downloads started in the browser (Chrome, Edge, Brave, Firefox)", v["extension_enabled"], 0)
        ttk.Label(box, text=status or "", style="Ok.TLabel" if "Connected" in status else "Hint.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, GAP))
        ttk.Label(box, wraplength=470, justify="left", text=(
            "Files you download in the browser are downloaded by Media Downloader, and the right-click menu "
            "gets \"Download link with Media Downloader\". Hold Alt while clicking a link to let the browser "
            "keep that download. The extension pairs itself; no token to copy.")).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(box, text="Install browser extension…", command=self._export_extension).grid(
            row=3, column=0, sticky="w", pady=(PAD, 0))

        box = group(tab, "Pairing token (only for manual installs)", row=1, column=0, sticky="ew", pady=(PAD, 0))
        box.grid_columnconfigure(0, weight=1)
        token = ttk.Entry(box, width=40)
        token.insert(0, self._token)
        token.configure(state="readonly")
        token.grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text="Copy", command=self._copy_token).grid(row=0, column=1, padx=(GAP, 0))

        box = group(tab, "Cookies for logged-in sites", row=2, column=0, sticky="ew", pady=(PAD, 0))
        box.grid_columnconfigure(1, weight=1)
        ttk.Label(box, text="cookies.txt file:").grid(row=0, column=0, sticky="w", padx=(0, PAD), pady=2)
        ttk.Entry(box, textvariable=v["cookies_file"]).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(box, text="Browse…", command=self._browse_cookies).grid(row=0, column=2, padx=(GAP, 0))
        ttk.Label(box, text="Or read from:").grid(row=1, column=0, sticky="w", padx=(0, PAD), pady=2)
        combo(box, v["cookies_browser"], ["None", "Firefox", "Chrome", "Edge", "Brave"], width=14).grid(
            row=1, column=1, sticky="w", pady=2)

    def _build_updates(self, tab: ttk.Frame) -> None:
        v = self.vars
        box = group(tab, "Automatic updates", row=0, column=0, sticky="ew")
        check(box, "Check for updates when Media Downloader starts", v["auto_check_updates"], 0)
        check(box, "Download verified updates automatically", v["auto_download_updates"], 1)
        row = ttk.Frame(box)
        row.grid(row=2, column=0, sticky="w", pady=(PAD, 0))
        ttk.Button(row, text="Check now", command=self._check_updates).pack(side="left")
        ttk.Button(row, text="Release notes", command=self._on_open_release).pack(side="left", padx=(GAP, 0))

        box = group(tab, "About", row=1, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Label(box, text=f"Media Downloader {APP_VERSION}", style="Title.TLabel").grid(row=0, column=0,
                                                                                          sticky="w")
        ttk.Label(box, text=f"{install_mode_name().capitalize()} installation", style="Hint.TLabel").grid(
            row=1, column=0, sticky="w")
        ttk.Label(box, text="Use only for content you own or have permission to download.",
                  style="Hint.TLabel").grid(row=2, column=0, sticky="w", pady=(GAP, 0))
        row = ttk.Frame(box)
        row.grid(row=3, column=0, sticky="w", pady=(PAD, 0))
        ttk.Button(row, text="Diagnostics…", command=self._on_open_diagnostics).pack(side="left")
        ttk.Button(row, text="Open log file", command=self._on_open_log).pack(side="left", padx=(GAP, 0))

    # ------------------------------------------------------------------ helpers
    def select_tab(self, name: str) -> None:
        name = _TAB_ALIASES.get(name, name)
        if name in self._pages:
            self.tabs.select(self._pages[name])

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.apply_button.configure(state="normal")

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _browse_download_folder(self) -> None:
        current = Path(str(self.vars["download_dir"].get())).expanduser()
        chosen = filedialog.askdirectory(parent=self, title="Choose download folder", mustexist=True,
                                         initialdir=str(current if current.exists() else Path.home()))
        if chosen:
            self.vars["download_dir"].set(chosen)

    def _browse_cookies(self) -> None:
        chosen = filedialog.askopenfilename(parent=self, title="Choose cookies.txt",
                                            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if chosen:
            self.vars["cookies_file"].set(chosen)

    def _copy_token(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._token)
        self.status.configure(text="Pairing token copied.")

    def _export_extension(self) -> None:
        if self._on_export_extension:
            self._on_export_extension()

    def _site_rows(self) -> list[tuple[str, str, str]]:
        return [tuple(self.sites.item(iid, "values")) for iid in self.sites.get_children()]  # type: ignore[misc]

    def _add_site(self) -> None:
        dialog = SiteRuleDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.sites.insert("", "end", values=dialog.result)
            self._mark_dirty()

    def _edit_site(self) -> None:
        selected = self.sites.selection()
        if not selected:
            return
        dialog = SiteRuleDialog(self, *self.sites.item(selected[0], "values"))
        self.wait_window(dialog)
        if dialog.result:
            self.sites.item(selected[0], values=dialog.result)
            self._mark_dirty()

    def _remove_site(self) -> None:
        selected = self.sites.selection()
        if selected:
            self.sites.delete(selected[0])
            self._mark_dirty()

    def payload(self) -> dict[str, Any]:
        payload = copy.deepcopy(self._original)
        for key, var in self.vars.items():
            payload[key] = var.get()
        payload["download_dir"] = str(Path(str(payload["download_dir"]).strip()).expanduser())
        try:
            payload["max_concurrent_downloads"] = int(payload["max_concurrent_downloads"])
        except (TypeError, ValueError):
            payload["max_concurrent_downloads"] = 6
        payload["folder_rules"] = rules_from_rows(
            [(h, f, "" if q == "Default" else q) for h, f, q in self._site_rows()])
        return payload

    # ------------------------------------------------------------------ buttons
    def apply(self) -> bool:
        if not self._dirty:
            return True
        payload = self.payload()
        try:
            self._on_save(payload)
        except Exception as exc:
            messagebox.showerror("Options", str(exc) or "Could not save the settings.", parent=self)
            return False
        self._original = copy.deepcopy(payload)
        self._dirty = False
        self.apply_button.configure(state="disabled")
        self.status.configure(text="Settings saved.")
        return True

    def ok(self) -> None:
        if self.apply():
            self.close()

    def _check_updates(self) -> None:
        if self.apply():
            self._on_check_updates()

    def can_leave(self) -> bool:
        return True  # Cancel discards changes, as in any Windows dialog

    def close(self) -> None:
        super().close()
        if self._on_close:
            self._on_close()
