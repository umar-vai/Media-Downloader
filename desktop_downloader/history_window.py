"""Download history page (inside the main window), modelled on IDM's download list.

* a sortable table of every completed, failed and cancelled download;
* right-click menu: Open, Show in folder, Open with editor, Resume / Download again,
  Copy links, Open web page, Properties, Remove from list, Delete file;
* Properties: everything known about the download (when, how long, how fast, from which
  website and link, where it was saved, resolution, codecs...).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from history_store import HistoryStore

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
WARNING = "#FFB84D"
DANGER = "#FF647C"

FILTERS = ["All", "Completed", "Failed", "Video", "Audio", "Missing"]
COLUMNS = (
    ("name", "File name", 340, "w"),
    ("size", "Size", 90, "e"),
    ("status", "Status", 100, "w"),
    ("quality", "Quality", 110, "w"),
    ("website", "Website", 150, "w"),
    ("date", "Date", 150, "w"),
)


def _human_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}" if index else f"{int(size)} B"


def _format_date(value: str, *, seconds: bool = False) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return parsed.strftime("%d %b %Y  %I:%M:%S %p" if seconds else "%d %b %Y  %I:%M %p")
    except Exception:
        return value


def _format_duration(seconds: Any) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - opening the user's own downloaded file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _reveal_path(path: Path) -> None:
    if os.name == "nt" and path.exists():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        target = path.parent if path.parent.exists() else Path.home()
        _open_path(target)


def entry_state(item: dict[str, Any]) -> str:
    """'Completed', 'Missing' (completed but the file is gone), 'Failed' or 'Cancelled'."""
    status = str(item.get("status") or "completed")
    if status == "completed":
        path = str(item.get("file_path") or "")
        return "Completed" if path and Path(path).exists() else "Missing"
    return status.title()


def matches(item: dict[str, Any], selected: str, query: str) -> bool:
    state = entry_state(item)
    if selected == "Completed" and state != "Completed":
        return False
    if selected == "Failed" and state not in {"Failed", "Cancelled"}:
        return False
    if selected == "Missing" and state != "Missing":
        return False
    if selected in {"Video", "Audio"} and item.get("mode") != selected:
        return False
    query = query.strip().lower()
    if not query:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "file_name", "creator", "platform", "website", "source_url", "page_url",
                    "file_path", "mode", "quality", "resolution")
    ).lower()
    return all(part in haystack for part in query.split())


def property_rows(item: dict[str, Any]) -> list[tuple[str, str]]:
    """The rows of the Properties dialog, in display order (empty values are skipped)."""
    state = entry_state(item)
    size = int(item.get("file_size") or 0)
    speed = int(item.get("avg_speed_bps") or 0)
    rows = [
        ("Title", str(item.get("title") or "")),
        ("Status", state),
        ("File name", str(item.get("file_name") or "")),
        ("Saved in", str(item.get("download_dir") or "")),
        ("Full path", str(item.get("file_path") or "")),
        ("Size", f"{_human_bytes(size)}  ({size:,} bytes)" if size else ""),
        ("Type", " • ".join(x for x in (str(item.get("mode") or ""), str(item.get("extension") or "")) if x)),
        ("Quality chosen", str(item.get("quality") or "")),
        ("Resolution", str(item.get("resolution") or "")),
        ("Length", _format_duration(item.get("duration_seconds"))),
        ("Video codec", str(item.get("video_codec") or "")),
        ("Audio codec", str(item.get("audio_codec") or "")),
        ("Bitrate", f"{item.get('bitrate_kbps')} kb/s" if item.get("bitrate_kbps") else ""),
        ("Website", str(item.get("website") or "")),
        ("Platform", str(item.get("platform") or "").title()),
        ("Creator / channel", str(item.get("creator") or "")),
        ("Web page", str(item.get("page_url") or "")),
        ("Download link", str(item.get("download_url") or item.get("source_url") or "")),
        ("Referer", str(item.get("referer") or "")),
        ("Started", _format_date(str(item.get("started_at") or ""), seconds=True)),
        ("Finished", _format_date(str(item.get("finished_at") or item.get("created_at") or ""), seconds=True)),
        ("Time taken", _format_duration(item.get("elapsed_seconds"))),
        ("Average speed", f"{_human_bytes(speed)}/s" if speed else ""),
        ("Attempts", str(item.get("attempts") or "") if int(item.get("attempts") or 0) > 1 else ""),
        ("Error", str(item.get("error") or "") if state in {"Failed", "Cancelled"} else ""),
    ]
    return [(label, value) for label, value in rows if value]


class PropertiesDialog(ctk.CTkToplevel):
    def __init__(self, master: tk.Misc, item: dict[str, Any], *, on_action: Callable[[str], None]) -> None:
        super().__init__(master)
        self.item = item
        self.title("Download properties")
        self.geometry("720x640")
        self.minsize(560, 420)
        self.configure(fg_color=BG)
        self.transient(master.winfo_toplevel())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        state = entry_state(item)
        colour = {"Completed": SUCCESS, "Missing": WARNING}.get(state, DANGER)
        head = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=str(item.get("title") or "Download"), text_color=TEXT,
                     font=("Segoe UI Semibold", 16), anchor="w", justify="left", wraplength=560).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(head, text=state.upper(), text_color=colour, font=("Segoe UI Semibold", 10),
                     anchor="w").grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

        body = ctk.CTkScrollableFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1,
                                      border_color=BORDER)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=14)
        body.grid_columnconfigure(1, weight=1)
        for row, (label, value) in enumerate(property_rows(item)):
            ctk.CTkLabel(body, text=label, text_color=MUTED, font=("Segoe UI", 10), anchor="nw").grid(
                row=row, column=0, sticky="nw", padx=(12, 14), pady=5)
            field = ctk.CTkTextbox(body, height=28 if len(value) < 70 else 56, fg_color=SURFACE_2,
                                   text_color=TEXT if label != "Error" else DANGER, font=("Segoe UI", 11),
                                   wrap="char", border_width=0, activate_scrollbars=False)
            field.insert("1.0", value)
            field.configure(state="disabled")
            field.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=3)
            ctk.CTkButton(body, text="Copy", width=52, height=26, fg_color=SURFACE_3, hover_color="#1B3153",
                          font=("Segoe UI", 9), command=lambda v=value: self._copy(v)).grid(
                row=row, column=2, padx=(0, 10), pady=3)

        actions = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        actions.grid(row=2, column=0, sticky="ew")
        buttons: list[tuple[str, str, bool]] = [
            ("Open", "open", state == "Completed"),
            ("Show in folder", "reveal", bool(item.get("file_path") or item.get("download_dir"))),
            ("Open web page", "page", bool(item.get("page_url") or item.get("source_url"))),
            ("Resume" if state in {"Failed", "Cancelled"} else "Download again",
             "resume" if state in {"Failed", "Cancelled"} else "redownload", True),
        ]
        for column, (text, action, enabled) in enumerate(buttons):
            ctk.CTkButton(actions, text=text, height=36, fg_color=PURPLE if column == 0 else SURFACE_3,
                          hover_color=PURPLE_HOVER if column == 0 else "#1B3153",
                          state="normal" if enabled else "disabled",
                          command=lambda a=action: self._act(a)).grid(row=0, column=column, padx=(16 if column == 0 else 4, 4), pady=12)
        ctk.CTkButton(actions, text="Close", width=80, height=36, fg_color="transparent", border_width=1,
                      border_color=BORDER, hover_color=SURFACE_2, command=self.destroy).grid(
            row=0, column=len(buttons), padx=(4, 16), pady=12, sticky="e")
        actions.grid_columnconfigure(len(buttons), weight=1)
        self._on_action = on_action
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self.focus_force)

    def _copy(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)

    def _act(self, action: str) -> None:
        self._on_action(action)
        if action in {"resume", "redownload"}:
            self.destroy()


class HistoryPage(ctk.CTkFrame):
    def __init__(
        self,
        master: tk.Misc,
        store: HistoryStore,
        *,
        on_edit: Callable[[Path], None],
        on_change: Callable[[], None] | None = None,
        on_redownload: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.store = store
        self.on_edit = on_edit
        self.on_change = on_change
        self.on_redownload = on_redownload
        self.search_var = ctk.StringVar()
        self.filter_var = ctk.StringVar(value="All")
        self.entries: list[dict[str, Any]] = []
        self._by_iid: dict[str, dict[str, Any]] = {}
        self._sort = ("date", True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header()
        self._build_toolbar()
        self._build_table()
        self._build_menu()
        self._build_footer()
        self.search_var.trace_add("write", lambda *_args: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(26, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="History", text_color=TEXT, font=("Segoe UI Semibold", 26), anchor="w").grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Every download, with all its details. Right-click a row for options, "
                                  "double-click to open.", text_color=MUTED, font=("Segoe UI", 12), anchor="w").grid(
            row=1, column=0, sticky="w", pady=(2, 0))
        self.summary_badge = ctk.CTkLabel(header, text="0 ITEMS", width=96, height=30, corner_radius=8,
                                          fg_color=SURFACE_2, text_color=CYAN, font=("Segoe UI Semibold", 10))
        self.summary_badge.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_toolbar(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=30, pady=(16, 10))
        toolbar.grid_columnconfigure(0, weight=1)
        # No textvariable: CustomTkinter hides the placeholder hint when one is set.
        self.search_entry = ctk.CTkEntry(toolbar, height=38, corner_radius=10,
                                         fg_color=SURFACE, border_color=BORDER,
                                         placeholder_text="Search name, website, link, quality…")
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.search_entry.bind("<KeyRelease>", lambda _e: self.search_var.set(self.search_entry.get()))
        ctk.CTkSegmentedButton(toolbar, values=FILTERS, variable=self.filter_var,
                               command=lambda _value: self.refresh(), height=36, fg_color=SURFACE_2,
                               selected_color=PURPLE, selected_hover_color=PURPLE_HOVER,
                               unselected_color=SURFACE_2, unselected_hover_color=SURFACE_3).grid(
            row=0, column=1, sticky="e")

    def _build_table(self) -> None:
        holder = tk.Frame(self, bg=BORDER, highlightthickness=0, bd=0)
        holder.grid(row=2, column=0, sticky="nsew", padx=30)
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("History.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        style.configure("History.Treeview.Heading", background=SURFACE_2, foreground=MUTED, relief="flat",
                        borderwidth=0, font=("Segoe UI Semibold", 9), padding=(8, 6))
        style.map("History.Treeview", background=[("selected", "#2A2266")], foreground=[("selected", TEXT)])
        style.map("History.Treeview.Heading", background=[("active", SURFACE_3)])
        style.layout("History.Treeview", [("History.Treeview.treearea", {"sticky": "nswe"})])

        self.tree = ttk.Treeview(holder, columns=[c[0] for c in COLUMNS], show="headings",
                                 style="History.Treeview", selectmode="extended")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, anchor=anchor, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, minwidth=60, anchor=anchor, stretch=key == "name")
        self.tree.tag_configure("odd", background="#0D1729")
        self.tree.tag_configure("missing", foreground=WARNING)
        self.tree.tag_configure("failed", foreground=DANGER)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scroll = ctk.CTkScrollbar(holder, orientation="vertical", command=self.tree.yview, fg_color=SURFACE,
                                  button_color=SURFACE_3, button_hover_color=BORDER, corner_radius=0)
        scroll.grid(row=0, column=1, sticky="ns", pady=1, padx=(0, 1))
        self.tree.configure(yscrollcommand=scroll.set)

        self.empty_label = ctk.CTkLabel(holder, text="No downloads yet — finished downloads appear here.",
                                        text_color=MUTED, fg_color=SURFACE, font=("Segoe UI", 12))

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda _e: self._default_action())
        self.tree.bind("<Delete>", lambda _e: self._remove_selected())
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Alt-Return>", lambda _e: self._properties())

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE_2, fg=TEXT, activebackground=PURPLE,
                            activeforeground=TEXT, disabledforeground="#4E5D78", bd=0, relief="flat",
                            font=("Segoe UI", 10))

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        footer.grid(row=3, column=0, sticky="ew", padx=30, pady=(12, 18))
        footer.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(footer, text="History is stored locally on this PC.", text_color=MUTED,
                                         font=("Segoe UI", 10), anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w", padx=16)
        ctk.CTkButton(footer, text="Properties", width=100, height=34, fg_color=SURFACE_3, hover_color="#1B3153",
                      command=self._properties).grid(row=0, column=1, padx=4, pady=10)
        ctk.CTkButton(footer, text="Remove missing", width=120, height=34, fg_color="transparent",
                      hover_color=SURFACE_2, border_width=1, border_color=BORDER, text_color=MUTED,
                      command=self.clear_missing).grid(row=0, column=2, padx=4, pady=10)
        ctk.CTkButton(footer, text="Clear history", width=110, height=34, fg_color="transparent",
                      hover_color="#30131B", border_width=1, border_color="#512332", text_color=DANGER,
                      command=self.clear_history).grid(row=0, column=3, padx=(4, 14), pady=10)

    # ------------------------------------------------------------------ data
    def _sort_key(self, item: dict[str, Any]) -> Any:
        key = self._sort[0]
        if key == "size":
            return int(item.get("file_size") or 0)
        if key == "name":
            return str(item.get("file_name") or item.get("title") or "").lower()
        if key == "status":
            return entry_state(item)
        if key == "quality":
            return str(item.get("resolution") or item.get("quality") or "")
        if key == "website":
            return str(item.get("website") or "")
        return str(item.get("finished_at") or item.get("created_at") or "")

    def _sort_by(self, key: str) -> None:
        current, descending = self._sort
        self._sort = (key, not descending if key == current else key in {"date", "size"})
        self.refresh()

    def refresh(self) -> None:
        selected_ids = {self._by_iid[i]["id"] for i in self.tree.selection() if i in self._by_iid}
        self.entries = self.store.list()
        visible = [item for item in self.entries if matches(item, self.filter_var.get(), self.search_var.get())]
        visible.sort(key=self._sort_key, reverse=self._sort[1])

        self.tree.delete(*self.tree.get_children())
        self._by_iid.clear()
        for index, item in enumerate(visible):
            state = entry_state(item)
            # The real resolution of the file when known, else the quality that was chosen.
            quality = item.get("resolution") or item.get("quality") or ""
            tags = ["odd"] if index % 2 else []
            if state == "Missing":
                tags.append("missing")
            elif state in {"Failed", "Cancelled"}:
                tags.append("failed")
            iid = self.tree.insert("", "end", values=(
                str(item.get("file_name") or item.get("title") or ""),
                _human_bytes(item.get("file_size")),
                state,
                str(quality),
                str(item.get("website") or str(item.get("platform") or "").title()),
                _format_date(str(item.get("finished_at") or item.get("created_at") or "")),
            ), tags=tags)
            self._by_iid[iid] = item
            if item["id"] in selected_ids:
                self.tree.selection_add(iid)

        if visible:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        total = len(self.entries)
        missing = sum(1 for item in self.entries if entry_state(item) == "Missing")
        failed = sum(1 for item in self.entries if entry_state(item) in {"Failed", "Cancelled"})
        self.summary_badge.configure(text=f"{total} ITEM" if total == 1 else f"{total} ITEMS")
        extra = (f" • {failed} failed" if failed else "") + (f" • {missing} missing" if missing else "")
        self.status_label.configure(text=f"Showing {len(visible)} of {total}{extra} • Stored locally")

    def _selected(self) -> list[dict[str, Any]]:
        return [self._by_iid[i] for i in self.tree.selection() if i in self._by_iid]

    # ------------------------------------------------------------------ events
    def _on_double_click(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) == "heading":
            return
        self._default_action()

    def _default_action(self) -> None:
        items = self._selected()
        if not items:
            return
        if entry_state(items[0]) == "Completed":
            self._open(items[0])
        else:
            self._properties()

    def _on_right_click(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus(row)
        items = self._selected()
        item = items[0]
        state = entry_state(item)
        single = len(items) == 1
        has_page = bool(item.get("page_url") or item.get("source_url"))

        menu = self.menu
        menu.delete(0, "end")

        def add(label: str, command: Callable[[], None], enabled: bool = True) -> None:
            menu.add_command(label=label, command=command, state="normal" if enabled else "disabled")

        add("Open", lambda: self._open(item), single and state == "Completed")
        add("Show in folder", lambda: self._reveal(item), single)
        add("Open in media editor", lambda: self._edit(item), single and state == "Completed")
        menu.add_separator()
        if state in {"Failed", "Cancelled"}:
            add("Resume download", lambda: self._redownload(items, resume=True))
        add("Download again", lambda: self._redownload(items, resume=False))
        menu.add_separator()
        add("Open web page", lambda: self._open_page(item), single and has_page)
        add("Copy web page link", lambda: self._copy(item.get("page_url") or item.get("source_url")),
            single and has_page)
        add("Copy download link", lambda: self._copy(item.get("download_url") or item.get("source_url")),
            single and bool(item.get("download_url") or item.get("source_url")))
        add("Copy file path", lambda: self._copy(item.get("file_path")), single and bool(item.get("file_path")))
        menu.add_separator()
        add("Remove from list", self._remove_selected)
        add("Delete file from disk…", self._delete_files,
            any(entry_state(x) == "Completed" for x in items))
        menu.add_separator()
        add("Properties", self._properties, single)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------ actions
    def _open(self, item: dict[str, Any]) -> None:
        path = Path(str(item.get("file_path") or ""))
        if not path.exists():
            messagebox.showwarning("Download history", "This file is no longer on the disk.", parent=self)
            self.refresh()
            return
        try:
            _open_path(path)
        except Exception as exc:
            messagebox.showerror("Download history", f"Could not open the file.\n\n{exc}", parent=self)

    def _reveal(self, item: dict[str, Any]) -> None:
        path_text = str(item.get("file_path") or "")
        path = Path(path_text) if path_text else Path(str(item.get("download_dir") or Path.home())) / "_"
        try:
            _reveal_path(path)
        except Exception as exc:
            messagebox.showerror("Download history", f"Could not open the folder.\n\n{exc}", parent=self)

    def _edit(self, item: dict[str, Any]) -> None:
        path = Path(str(item.get("file_path") or ""))
        if path.exists():
            self.on_edit(path)

    def _open_page(self, item: dict[str, Any]) -> None:
        url = str(item.get("page_url") or item.get("source_url") or "")
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def _copy(self, value: Any) -> None:
        text = str(value or "")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_label.configure(text="Copied to the clipboard.")

    def _redownload(self, items: list[dict[str, Any]], *, resume: bool) -> None:
        if self.on_redownload is None:
            return
        for item in items:
            self.on_redownload(item, resume=resume and entry_state(item) in {"Failed", "Cancelled"})

    def _properties(self) -> None:
        items = self._selected()
        if not items:
            return
        item = items[0]

        def act(action: str) -> None:
            if action == "open":
                self._open(item)
            elif action == "reveal":
                self._reveal(item)
            elif action == "page":
                self._open_page(item)
            elif action in {"resume", "redownload"}:
                self._redownload([item], resume=action == "resume")

        PropertiesDialog(self, item, on_action=act)

    def _remove_selected(self) -> None:
        items = self._selected()
        if not items:
            return
        for item in items:
            self.store.remove(str(item["id"]))
        if self.on_change:
            self.on_change()
        self.refresh()
        self.status_label.configure(text=f"Removed {len(items)} item{'s' if len(items) != 1 else ''} from the list.")

    def _delete_files(self) -> None:
        items = [item for item in self._selected() if entry_state(item) == "Completed"]
        if not items:
            return
        names = "\n".join(f"• {Path(str(item['file_path'])).name}" for item in items[:8])
        more = f"\n…and {len(items) - 8} more" if len(items) > 8 else ""
        if not messagebox.askyesno(
            "Delete file",
            f"Permanently delete {len(items)} file{'s' if len(items) != 1 else ''} from the disk?\n\n"
            f"{names}{more}\n\nThis cannot be undone.",
            icon="warning",
            parent=self,
        ):
            return
        failed: list[str] = []
        for item in items:
            try:
                Path(str(item["file_path"])).unlink(missing_ok=True)
                self.store.remove(str(item["id"]))
            except OSError as exc:
                failed.append(f"{Path(str(item['file_path'])).name}: {exc}")
        if self.on_change:
            self.on_change()
        self.refresh()
        if failed:
            messagebox.showerror("Delete file", "Some files could not be deleted:\n\n" + "\n".join(failed),
                                 parent=self)

    def clear_missing(self) -> None:
        removed = self.store.prune_missing()
        if self.on_change:
            self.on_change()
        self.refresh()
        self.status_label.configure(text=f"Removed {removed} missing item{'s' if removed != 1 else ''}.")

    def clear_history(self) -> None:
        if not self.entries:
            return
        if not messagebox.askyesno(
            "Download history",
            "Clear all download history?\n\nThis does not delete any downloaded files.",
            parent=self,
        ):
            return
        removed = self.store.clear()
        if self.on_change:
            self.on_change()
        self.refresh()
        self.status_label.configure(text=f"Cleared {removed} history item{'s' if removed != 1 else ''}.")
