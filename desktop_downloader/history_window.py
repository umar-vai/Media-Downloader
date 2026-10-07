from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk
from tkinter import messagebox

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
    return f"{size:.1f} {units[index]}"


def _format_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return parsed.strftime("%d %b %Y • %I:%M %p")
    except Exception:
        return value or "Unknown date"


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
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


class HistoryWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master: ctk.CTkBaseClass,
        store: HistoryStore,
        *,
        on_edit: Callable[[Path], None],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.store = store
        self.on_edit = on_edit
        self.on_change = on_change

        self.title("Download History")
        self.geometry("980x720")
        self.minsize(820, 600)
        self.configure(fg_color=BG)
        self.transient(master)

        self.search_var = ctk.StringVar()
        self.filter_var = ctk.StringVar(value="All")
        self.entries: list[dict[str, Any]] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_list()
        self._build_footer()

        self.search_var.trace_add("write", lambda *_args: self.refresh())
        self.after(120, self.refresh)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="DOWNLOAD HISTORY",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            header,
            text="Search recent downloads, reopen files, reveal them in Explorer, or send them to the editor.",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

        self.summary_badge = ctk.CTkLabel(
            header,
            text="0 ITEMS",
            width=88,
            height=28,
            corner_radius=8,
            fg_color=SURFACE_2,
            text_color=CYAN,
            font=("Segoe UI Semibold", 9),
        )
        self.summary_badge.grid(row=0, column=1, rowspan=2, padx=20)

    def _build_toolbar(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 8))
        toolbar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            toolbar,
            textvariable=self.search_var,
            height=38,
            corner_radius=10,
            fg_color=SURFACE,
            border_color=BORDER,
            placeholder_text="Search title, creator, platform, file name or URL…",
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.filter_control = ctk.CTkSegmentedButton(
            toolbar,
            values=["All", "Video", "Audio", "Missing"],
            variable=self.filter_var,
            command=lambda _value: self.refresh(),
            height=36,
            fg_color=SURFACE_2,
            selected_color=PURPLE,
            selected_hover_color=PURPLE_HOVER,
            unselected_color=SURFACE_2,
            unselected_hover_color=SURFACE_3,
        )
        self.filter_control.grid(row=0, column=1, sticky="e")

    def _build_list(self) -> None:
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.list_frame.grid_columnconfigure(0, weight=1)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            footer,
            text="History is stored locally on this PC.",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=18)

        ctk.CTkButton(
            footer,
            text="Clear missing",
            width=100,
            height=36,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.clear_missing,
        ).grid(row=0, column=1, padx=5, pady=12)

        ctk.CTkButton(
            footer,
            text="Clear history",
            width=100,
            height=36,
            fg_color="transparent",
            hover_color="#30131B",
            border_width=1,
            border_color="#512332",
            text_color=DANGER,
            command=self.clear_history,
        ).grid(row=0, column=2, padx=(5, 18), pady=12)

    def _matches(self, item: dict[str, Any]) -> bool:
        path = Path(str(item.get("file_path") or ""))
        selected = self.filter_var.get()
        if selected == "Video" and item.get("mode") != "Video":
            return False
        if selected == "Audio" and item.get("mode") != "Audio":
            return False
        if selected == "Missing" and path.exists():
            return False

        query = self.search_var.get().strip().lower()
        if not query:
            return True

        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "creator", "platform", "source_url", "file_path", "mode", "quality")
        ).lower()
        return query in haystack

    def refresh(self) -> None:
        self.entries = self.store.list()
        visible = [item for item in self.entries if self._matches(item)]

        for child in self.list_frame.winfo_children():
            child.destroy()

        total = len(self.entries)
        missing = sum(1 for item in self.entries if not Path(str(item["file_path"])).exists())
        self.summary_badge.configure(text=f"{total} ITEM" if total == 1 else f"{total} ITEMS")
        self.status_label.configure(
            text=(
                f"Showing {len(visible)} of {total} downloads"
                + (f" • {missing} missing" if missing else "")
                + " • Stored locally"
            )
        )

        if not visible:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=60)
            ctk.CTkLabel(
                empty,
                text="No matching downloads",
                text_color=TEXT,
                font=("Segoe UI Semibold", 15),
            ).pack()
            ctk.CTkLabel(
                empty,
                text="Completed downloads will appear here automatically.",
                text_color=MUTED,
                font=("Segoe UI", 10),
            ).pack(pady=(5, 0))
            return

        for row, item in enumerate(visible):
            self._render_item(row, item)

    def _render_item(self, row: int, item: dict[str, Any]) -> None:
        path = Path(str(item["file_path"]))
        exists = path.exists()
        mode = str(item.get("mode") or "Video")
        platform = str(item.get("platform") or "").title() or "Media"
        quality = str(item.get("quality") or "")
        creator = str(item.get("creator") or "")
        size = _human_bytes(item.get("file_size"))
        detail_parts = [platform, mode]
        if quality:
            detail_parts.append(quality)
        if creator:
            detail_parts.append(creator)
        if size:
            detail_parts.append(size)
        detail_parts.append(_format_date(str(item.get("created_at") or "")))

        card = ctk.CTkFrame(
            self.list_frame,
            fg_color=SURFACE_2,
            corner_radius=11,
            border_width=1,
            border_color=BORDER if exists else "#512332",
        )
        card.grid(row=row, column=0, sticky="ew", padx=5, pady=5)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text=str(item.get("title") or path.stem),
            text_color=TEXT,
            font=("Segoe UI Semibold", 12),
            anchor="w",
            justify="left",
            wraplength=590,
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            card,
            text=" • ".join(detail_parts),
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=12)

        ctk.CTkLabel(
            card,
            text=str(path),
            text_color="#647795" if exists else DANGER,
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=620,
        ).grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 10))

        badge = ctk.CTkLabel(
            card,
            text="READY" if exists else "MISSING",
            width=62,
            height=26,
            corner_radius=8,
            fg_color="#0E3025" if exists else "#351722",
            text_color=SUCCESS if exists else DANGER,
            font=("Segoe UI Semibold", 8),
        )
        badge.grid(row=0, column=1, padx=(4, 12), pady=(10, 2))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=1, column=1, rowspan=2, sticky="e", padx=(4, 12), pady=(2, 9))

        for column, (label, command, enabled) in enumerate(
            [
                ("Open", lambda p=path: self._open_file(p), exists),
                ("Folder", lambda p=path: self._reveal_file(p), True),
                ("Edit", lambda p=path: self._edit_file(p), exists),
                ("Copy link", lambda item=item: self._copy_link(item), bool(item.get("source_url"))),
                ("Remove", lambda entry_id=str(item["id"]): self.remove_entry(entry_id), True),
            ]
        ):
            ctk.CTkButton(
                actions,
                text=label,
                width=72,
                height=28,
                corner_radius=8,
                fg_color=SURFACE_3 if label != "Remove" else "transparent",
                hover_color="#1B3153" if label != "Remove" else "#30131B",
                border_width=1,
                border_color=BORDER if label != "Remove" else "#512332",
                text_color=TEXT if label != "Remove" else DANGER,
                command=command,
                state="normal" if enabled else "disabled",
                font=("Segoe UI", 8),
            ).grid(row=0, column=column, padx=2)

    def _open_file(self, path: Path) -> None:
        if not path.exists():
            self.refresh()
            return
        try:
            _open_path(path)
        except Exception as exc:
            messagebox.showerror("Download History", f"Could not open the file.\n\n{exc}", parent=self)

    def _reveal_file(self, path: Path) -> None:
        try:
            _reveal_path(path)
        except Exception as exc:
            messagebox.showerror("Download History", f"Could not open the file location.\n\n{exc}", parent=self)

    def _edit_file(self, path: Path) -> None:
        if not path.exists():
            self.refresh()
            return
        self.on_edit(path)

    def _copy_link(self, item: dict[str, Any]) -> None:
        url = str(item.get("source_url") or "")
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.status_label.configure(text="Source link copied to clipboard.")
        self.after(1600, lambda: self.refresh() if self.winfo_exists() else None)

    def remove_entry(self, entry_id: str) -> None:
        if self.store.remove(entry_id):
            if self.on_change:
                self.on_change()
            self.refresh()

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
            "Download History",
            "Clear all download history?\n\nThis does not delete any downloaded files.",
            parent=self,
        ):
            return
        removed = self.store.clear()
        if self.on_change:
            self.on_change()
        self.refresh()
        self.status_label.configure(text=f"Cleared {removed} history item{'s' if removed != 1 else ''}.")
