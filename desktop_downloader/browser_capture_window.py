from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import customtkinter as ctk
from tkinter import messagebox

from browser_capture import BrowserCaptureBridge, CaptureStore

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


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _time_label(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%I:%M:%S %p")
    except Exception:
        return ""


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


class BrowserCaptureWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master: ctk.CTkBaseClass,
        *,
        store: CaptureStore,
        bridge: BrowserCaptureBridge | None,
        token: str,
        extension_dir: Path | None,
        on_download: Callable[[dict[str, Any], bool], None],
        on_regenerate_token: Callable[[], str],
        on_open_release: Callable[[], None],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Browser Capture")
        self.geometry("980x720")
        self.minsize(820, 600)
        self.configure(fg_color=BG)
        self.transient(master)

        self.store = store
        self.bridge = bridge
        self.token = token
        self.extension_dir = extension_dir
        self.on_download = on_download
        self.on_regenerate_token = on_regenerate_token
        self.on_open_release = on_open_release
        self.on_change = on_change

        self.search_var = ctk.StringVar()
        self.kind_var = ctk.StringVar(value="All")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_pairing()
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
            text="BROWSER CAPTURE",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            header,
            text="Capture direct media, HLS/DASH streams, and page fallbacks from Chrome or Edge.",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

        running = bool(self.bridge and self.bridge.running)
        self.status_badge = ctk.CTkLabel(
            header,
            text="CONNECTED" if running else "OFFLINE",
            width=92,
            height=28,
            corner_radius=8,
            fg_color="#0E3025" if running else "#351722",
            text_color=SUCCESS if running else DANGER,
            font=("Segoe UI Semibold", 9),
        )
        self.status_badge.grid(row=0, column=1, rowspan=2, padx=20)

    def _build_pairing(self) -> None:
        card = ctk.CTkFrame(
            self,
            fg_color=SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 10))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="EXTENSION PAIRING",
            text_color=CYAN,
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=14, pady=(12, 7))

        ctk.CTkLabel(card, text="Port", text_color=MUTED, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", padx=(14, 6))
        self.port_entry = ctk.CTkEntry(card, width=90, height=34)
        self.port_entry.grid(row=1, column=1, sticky="w")
        self.port_entry.insert(0, str(self.bridge.port if self.bridge else ""))
        self.port_entry.configure(state="disabled")

        ctk.CTkLabel(card, text="Token", text_color=MUTED, font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", padx=(14, 6), pady=(8, 12))
        self.token_entry = ctk.CTkEntry(card, height=34)
        self.token_entry.grid(row=2, column=1, sticky="ew", pady=(8, 12))
        self.token_entry.insert(0, self.token)
        self.token_entry.configure(state="disabled")

        ctk.CTkButton(
            card,
            text="Copy pairing",
            width=100,
            height=34,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self.copy_pairing,
        ).grid(row=1, column=2, padx=(10, 5))

        ctk.CTkButton(
            card,
            text="New token",
            width=88,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.regenerate_token,
        ).grid(row=1, column=3, padx=(5, 14))

        if self.extension_dir is not None and self.extension_dir.exists():
            extension_text = "Open extension folder"
            extension_command = lambda: _open_path(self.extension_dir)
        else:
            extension_text = "Get extension"
            extension_command = self.on_open_release

        ctk.CTkButton(
            card,
            text=extension_text,
            width=130,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=extension_command,
        ).grid(row=2, column=2, padx=(10, 5), pady=(8, 12))

        ctk.CTkLabel(
            card,
            text="Load the extension in Chrome/Edge, paste the pairing code once, then play a video. Detected streams appear below.",
            text_color=MUTED,
            font=("Segoe UI", 8),
            justify="left",
            wraplength=250,
        ).grid(row=2, column=3, sticky="w", padx=(5, 14), pady=(8, 12))

        toolbar = ctk.CTkFrame(card, fg_color="transparent")
        toolbar.grid(row=3, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 12))
        toolbar.grid_columnconfigure(0, weight=1)

        ctk.CTkEntry(
            toolbar,
            textvariable=self.search_var,
            height=36,
            placeholder_text="Search captured title, host, stream type or URL…",
            fg_color=SURFACE_2,
            border_color=BORDER,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkSegmentedButton(
            toolbar,
            values=["All", "HLS", "DASH", "Direct", "Page"],
            variable=self.kind_var,
            command=lambda _value: self.refresh(),
            height=34,
            fg_color=SURFACE_2,
            selected_color=PURPLE,
            selected_hover_color=PURPLE_HOVER,
            unselected_color=SURFACE_2,
            unselected_hover_color=SURFACE_3,
        ).grid(row=0, column=1)

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

        self.footer_label = ctk.CTkLabel(
            footer,
            text="Captured request headers stay in memory and are not written to download history.",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.footer_label.grid(row=0, column=0, sticky="w", padx=18)

        ctk.CTkButton(
            footer,
            text="Clear captures",
            width=104,
            height=36,
            fg_color="transparent",
            hover_color="#30131B",
            border_width=1,
            border_color="#512332",
            text_color=DANGER,
            command=self.clear_captures,
        ).grid(row=0, column=1, padx=(5, 18), pady=12)

    def copy_pairing(self) -> None:
        port = self.bridge.port if self.bridge else ""
        value = f"{port}|{self.token}"
        self.clipboard_clear()
        self.clipboard_append(value)
        self.footer_label.configure(text="Pairing code copied. Paste it into the browser extension.")
        self.after(1800, self._restore_footer)

    def regenerate_token(self) -> None:
        if not messagebox.askyesno(
            "Browser Capture",
            "Generate a new pairing token?\n\nThe browser extension will need to be paired again.",
            parent=self,
        ):
            return
        self.token = self.on_regenerate_token()
        self.token_entry.configure(state="normal")
        self.token_entry.delete(0, "end")
        self.token_entry.insert(0, self.token)
        self.token_entry.configure(state="disabled")
        self.footer_label.configure(text="New token generated. Pair the extension again.")
        self.after(2200, self._restore_footer)

    def _restore_footer(self) -> None:
        if self.winfo_exists():
            self.footer_label.configure(text="Captured request headers stay in memory and are not written to download history.")

    def _matches(self, item: dict[str, Any]) -> bool:
        selected = self.kind_var.get().lower()
        kind = str(item.get("kind") or "unknown").lower()
        if selected != "all" and kind != selected:
            return False
        query = self.search_var.get().strip().lower()
        if not query:
            return True
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("url") or ""),
                str(item.get("page_url") or ""),
                kind,
                str(item.get("content_type") or ""),
                _host(str(item.get("url") or "")),
            ]
        ).lower()
        return query in haystack

    def refresh(self) -> None:
        items = [item for item in self.store.list() if self._matches(item)]
        for child in self.list_frame.winfo_children():
            child.destroy()

        if not items:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", pady=65)
            ctk.CTkLabel(
                empty,
                text="No media captured yet",
                text_color=TEXT,
                font=("Segoe UI Semibold", 15),
            ).pack()
            ctk.CTkLabel(
                empty,
                text="Keep this app open, pair the extension, then start playback in the browser.",
                text_color=MUTED,
                font=("Segoe UI", 10),
            ).pack(pady=(5, 0))
            return

        for row, item in enumerate(items):
            self._render_capture(row, item)

    def _render_capture(self, row: int, item: dict[str, Any]) -> None:
        kind = str(item.get("kind") or "unknown").upper()
        host = _host(str(item.get("url") or "")) or "Media host"
        content_type = str(item.get("content_type") or "")
        details = [host, kind]
        if content_type:
            details.append(content_type)
        captured = _time_label(item.get("captured_at"))
        if captured:
            details.append(captured)

        card = ctk.CTkFrame(
            self.list_frame,
            fg_color=SURFACE_2,
            corner_radius=11,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(row=row, column=0, sticky="ew", padx=5, pady=5)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text=str(item.get("title") or "Captured media"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 12),
            anchor="w",
            justify="left",
            wraplength=730,
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            card,
            text=" • ".join(details),
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12)

        display_url = str(item.get("url") or "")
        if len(display_url) > 140:
            display_url = display_url[:137] + "…"
        ctk.CTkLabel(
            card,
            text=display_url,
            text_color="#647795",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=830,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 7))

        ctk.CTkLabel(
            card,
            text=kind,
            width=62,
            height=26,
            corner_radius=8,
            fg_color="#0E3025",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 8),
        ).grid(row=0, column=1, padx=(4, 12), pady=(10, 2))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10))

        buttons = [
            ("Download", lambda item=item: self.on_download(dict(item), False), PURPLE, PURPLE_HOVER, TEXT),
            ("Edit & download", lambda item=item: self.on_download(dict(item), True), SURFACE_3, "#1B3153", TEXT),
            ("Copy URL", lambda item=item: self.copy_url(item), SURFACE_3, "#1B3153", TEXT),
            ("Remove", lambda capture_id=str(item.get("id") or ""): self.remove_capture(capture_id), "transparent", "#30131B", DANGER),
        ]
        for column, (label, command, fg, hover, text_color) in enumerate(buttons):
            ctk.CTkButton(
                actions,
                text=label,
                width=104 if label == "Edit & download" else 86,
                height=30,
                corner_radius=8,
                fg_color=fg,
                hover_color=hover,
                border_width=1,
                border_color=BORDER if label != "Remove" else "#512332",
                text_color=text_color,
                command=command,
                font=("Segoe UI", 8),
            ).grid(row=0, column=column, padx=2)

    def copy_url(self, item: dict[str, Any]) -> None:
        url = str(item.get("url") or "")
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.footer_label.configure(text="Captured media URL copied.")
        self.after(1600, self._restore_footer)

    def remove_capture(self, capture_id: str) -> None:
        self.store.remove(capture_id)
        if self.on_change:
            self.on_change()
        self.refresh()

    def clear_captures(self) -> None:
        count = self.store.clear()
        if self.on_change:
            self.on_change()
        self.refresh()
        self.footer_label.configure(text=f"Cleared {count} captured stream{'s' if count != 1 else ''}.")
        self.after(1800, self._restore_footer)
