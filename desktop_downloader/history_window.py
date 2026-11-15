"""Download details: the Properties dialog and the history helpers behind it.

Finished, failed and cancelled downloads are listed in the main window (Finished category);
this module formats what is known about one of them (when, how long, how fast, from which
website and link, where it was saved, resolution, codecs...).
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

import native
from native import PAD
from theme import C

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


# Rows shown on the "Media" tab; everything else is on "General".
_MEDIA_ROWS = {"Quality chosen", "Resolution", "Length", "Video codec", "Audio codec", "Bitrate", "Platform",
               "Creator / channel", "Referer", "Attempts"}


class PropertiesDialog(native.Dialog):
    """Download properties, laid out like a Windows file Properties sheet."""

    def __init__(self, master: tk.Misc, item: dict[str, Any], *, on_action: Callable[[str], None]) -> None:
        super().__init__(master, f"{item.get('file_name') or item.get('title') or 'Download'} Properties")
        self.item = item
        self._on_action = on_action
        state = entry_state(item)
        # Plain text like a Windows Properties sheet; only a problem is shown in red.
        colour = C["DANGER"] if state in {"Failed", "Missing"} else C["TEXT"]

        head = ttk.Frame(self.body)
        head.pack(fill="x", pady=(0, PAD))
        ttk.Label(head, text=str(item.get("title") or item.get("file_name") or "Download"), style="Title.TLabel",
                  wraplength=520, justify="left").pack(anchor="w")
        ttk.Label(head, text=state, foreground=colour).pack(anchor="w")

        tabs = ttk.Notebook(self.body)
        tabs.pack(fill="both", expand=True)
        rows = property_rows(item)
        general = [row for row in rows if row[0] not in _MEDIA_ROWS]
        media = [row for row in rows if row[0] in _MEDIA_ROWS]
        for title, part in (("General", general), ("Media", media)):
            if not part:
                continue
            page = ttk.Frame(tabs, padding=(PAD, PAD, PAD, PAD))
            page.grid_columnconfigure(1, weight=1)
            tabs.add(page, text=title)
            for index, (label, value) in enumerate(part):
                ttk.Label(page, text=f"{label}:").grid(row=index, column=0, sticky="nw", padx=(0, PAD), pady=2)
                if label == "Error":
                    box = tk.Text(page, height=3 if label != "Error" else 5, width=58, wrap="word", relief="flat",
                                  font=native.font(self), bd=0, highlightthickness=1,
                                  highlightbackground=C["BORDER"], bg=C["SURFACE"],
                                  fg=C["DANGER"] if label == "Error" else C["TEXT"])
                    box.insert("1.0", value)
                    box.configure(state="disabled")
                    box.grid(row=index, column=1, sticky="ew", pady=2)
                else:
                    field = ttk.Entry(page, width=60)
                    field.insert(0, value)
                    field.configure(state="readonly")
                    field.grid(row=index, column=1, sticky="ew", pady=2)

        resumable = state in {"Failed", "Cancelled"}
        self.add_button("Close", self.close)
        self.add_button("Resume" if resumable else "Download again",
                        lambda: self._act("resume" if resumable else "redownload"), width=15)
        page_button = self.add_button("Open web page", lambda: self._act("page"), width=15)
        folder_button = self.add_button("Open folder", lambda: self._act("reveal"))
        open_button = self.add_button("Open", lambda: self._act("open"), default=state == "Completed")
        if state != "Completed":
            open_button.configure(state="disabled")
        if not (item.get("file_path") or item.get("download_dir")):
            folder_button.configure(state="disabled")
        if not (item.get("page_url") or item.get("source_url")):
            page_button.configure(state="disabled")
        self.show()

    def _act(self, action: str) -> None:
        self._on_action(action)
        if action in {"resume", "redownload"}:
            self.close()
