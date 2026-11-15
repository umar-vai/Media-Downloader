"""Download details: the Properties dialog and the history helpers behind it.

Finished, failed and cancelled downloads are listed in the main window (Finished category);
this module formats what is known about one of them (when, how long, how fast, from which
website and link, where it was saved, resolution, codecs...).
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from theme import C, ctk_button

BG = C["BG"]
SURFACE = C["SURFACE"]
SURFACE_2 = C["SURFACE_2"]
SURFACE_3 = C["SURFACE_3"]
BORDER = C["BORDER"]
TEXT = C["TEXT"]
MUTED = C["MUTED"]
CYAN = C["CYAN"]
SUCCESS = C["SUCCESS"]
WARNING = C["WARNING"]
DANGER = C["DANGER"]

FILTERS = ["All", "Completed", "Failed", "Video", "Audio", "Missing"]


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
        head = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=str(item.get("title") or "Download"), text_color=TEXT,
                     font=("Segoe UI Semibold", 16), anchor="w", justify="left", wraplength=560).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(head, text=state.upper(), text_color=colour, font=("Segoe UI Semibold", 10),
                     anchor="w").grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

        body = ctk.CTkScrollableFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1,
                                      border_color=BORDER, scrollbar_button_color=SURFACE_3)
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
            ctk.CTkButton(body, text="Copy", width=52, height=26, font=("Segoe UI", 9), **ctk_button(),
                          command=lambda v=value: self._copy(v)).grid(
                row=row, column=2, padx=(0, 10), pady=3)

        actions = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        actions.grid(row=2, column=0, sticky="ew")
        buttons: list[tuple[str, str, bool]] = [
            ("Open", "open", state == "Completed"),
            ("Show in folder", "reveal", bool(item.get("file_path") or item.get("download_dir"))),
            ("Open web page", "page", bool(item.get("page_url") or item.get("source_url"))),
            ("Resume" if state in {"Failed", "Cancelled"} else "Download again",
             "resume" if state in {"Failed", "Cancelled"} else "redownload", True),
        ]
        for column, (text, action, enabled) in enumerate(buttons):
            ctk.CTkButton(actions, text=text, height=36, **ctk_button("primary" if column == 0 else "secondary"),
                          state="normal" if enabled else "disabled",
                          command=lambda a=action: self._act(a)).grid(row=0, column=column, padx=(16 if column == 0 else 4, 4), pady=12)
        ctk.CTkButton(actions, text="Close", width=80, height=36, **ctk_button("ghost"), command=self.destroy).grid(
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
