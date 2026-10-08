from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import customtkinter as ctk
from tkinter import messagebox

from browser_capture import BrowserCaptureBridge, CaptureStore
from capture_quality import capture_rank
from network_proxy import safe_proxy_label

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


class BrowserCapturePanel(ctk.CTkFrame):
    """Embedded Browser Capture experience; intentionally not a second window."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        *,
        store: CaptureStore,
        get_bridge: Callable[[], BrowserCaptureBridge | None],
        get_pairing: Callable[[], str],
        extension_dir: Path | None,
        on_download: Callable[[dict[str, Any], bool], None],
        on_cancel: Callable[[str], None],
        on_remove: Callable[[str], None],
        on_clear: Callable[[], None],
        get_job_state: Callable[[str], dict[str, Any] | None],
        get_queue_summary: Callable[[], tuple[int, int]],
        on_regenerate_token: Callable[[], str],
        on_open_release: Callable[[], None],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.store = store
        self.get_bridge = get_bridge
        self.get_pairing = get_pairing
        self.extension_dir = extension_dir
        self.on_download = on_download
        self.on_cancel = on_cancel
        self.on_remove = on_remove
        self.on_clear = on_clear
        self.get_job_state = get_job_state
        self.get_queue_summary = get_queue_summary
        self.on_regenerate_token = on_regenerate_token
        self.on_open_release = on_open_release

        self.search_var = ctk.StringVar()
        self.kind_var = ctk.StringVar(value="All")
        self._network_label = ""
        self._network_label_at = 0.0
        self._recommended_ids: set[str] = set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self._build_connection()
        self._build_toolbar()
        self._build_list()
        self.search_var.trace_add("write", lambda *_args: self.refresh())

    def _card(self, **kwargs: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            self,
            fg_color=SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            **kwargs,
        )

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Browser Capture",
            text_color=TEXT,
            font=("Segoe UI Semibold", 29),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Play a video in Chrome or Edge. Best detected quality is selected automatically.",
            text_color=MUTED,
            font=("Segoe UI", 12),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.queue_label = ctk.CTkLabel(
            header,
            text="No active downloads",
            height=30,
            corner_radius=9,
            fg_color=SURFACE_2,
            text_color=MUTED,
            font=("Segoe UI Semibold", 9),
        )
        self.queue_label.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_connection(self) -> None:
        card = self._card()
        card.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        self.status_badge = ctk.CTkLabel(
            card,
            text="EXTENSION OFFLINE",
            width=130,
            height=30,
            corner_radius=9,
            fg_color="#351722",
            text_color=DANGER,
            font=("Segoe UI Semibold", 9),
        )
        self.status_badge.grid(row=0, column=0, padx=(16, 12), pady=14)

        self.connection_text = ctk.CTkLabel(
            card,
            text="Open Media Downloader and pair the browser extension once.",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.connection_text.grid(row=0, column=1, sticky="w")

        ctk.CTkButton(
            card,
            text="Copy pairing",
            width=104,
            height=32,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            command=self.copy_pairing,
        ).grid(row=0, column=2, padx=(8, 4))

        if self.extension_dir is not None and self.extension_dir.exists():
            extension_text = "Extension folder"
            extension_command = lambda: _open_path(self.extension_dir)
        else:
            extension_text = "Get extension"
            extension_command = self.on_open_release

        ctk.CTkButton(
            card,
            text=extension_text,
            width=112,
            height=32,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=extension_command,
        ).grid(row=0, column=3, padx=4)

        ctk.CTkButton(
            card,
            text="New token",
            width=82,
            height=32,
            fg_color="transparent",
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.regenerate_token,
        ).grid(row=0, column=4, padx=(4, 16))

    def _build_toolbar(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 10))
        toolbar.grid_columnconfigure(0, weight=1)

        ctk.CTkEntry(
            toolbar,
            textvariable=self.search_var,
            height=38,
            placeholder_text="Search detected video, website or stream type…",
            fg_color=SURFACE_2,
            border_color=BORDER,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkSegmentedButton(
            toolbar,
            values=["All", "HLS", "DASH", "Direct", "Page"],
            variable=self.kind_var,
            command=lambda _value: self.refresh(),
            height=36,
            fg_color=SURFACE_2,
            selected_color=PURPLE,
            selected_hover_color=PURPLE_HOVER,
            unselected_color=SURFACE_2,
            unselected_hover_color=SURFACE_3,
        ).grid(row=0, column=1)

        ctk.CTkButton(
            toolbar,
            text="Clear",
            width=72,
            height=36,
            fg_color="transparent",
            hover_color="#30131B",
            border_width=1,
            border_color="#512332",
            text_color=DANGER,
            command=self.on_clear,
        ).grid(row=0, column=2, padx=(10, 0))

    def _build_list(self) -> None:
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
        )
        self.list_frame.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 22))
        self.list_frame.grid_columnconfigure(0, weight=1)

    def copy_pairing(self) -> None:
        value = self.get_pairing()
        if not value:
            self.connection_text.configure(text="Browser Capture bridge is offline.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.connection_text.configure(text="Pairing code copied. Paste it into the extension once.")

    def regenerate_token(self) -> None:
        if not messagebox.askyesno(
            "Browser Capture",
            "Generate a new pairing token? The browser extension will need to be paired again.",
            parent=self.winfo_toplevel(),
        ):
            return
        self.on_regenerate_token()
        self.connection_text.configure(text="New token generated. Pair the extension again.")

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
        bridge = self.get_bridge()
        running = bool(bridge and bridge.running)
        self.status_badge.configure(
            text="EXTENSION READY" if running else "EXTENSION OFFLINE",
            fg_color="#0E3025" if running else "#351722",
            text_color=SUCCESS if running else DANGER,
        )
        now = time.monotonic()
        if not self._network_label or (now - self._network_label_at) >= 2.0:
            self._network_label = safe_proxy_label()
            self._network_label_at = now
        if running:
            self.connection_text.configure(
                text=f"Browser bridge ready on port {bridge.port}. {self._network_label}."
            )
        else:
            self.connection_text.configure(
                text=f"Browser Capture bridge is offline. {self._network_label}."
            )

        queued, active = self.get_queue_summary()
        if active:
            queue_text = f"1 downloading • {queued} queued" if queued else "1 download active"
            queue_color = CYAN
        elif queued:
            queue_text = f"{queued} queued"
            queue_color = WARNING
        else:
            queue_text = "No active downloads"
            queue_color = MUTED
        self.queue_label.configure(text=queue_text, text_color=queue_color)

        items = [item for item in self.store.list() if self._matches(item)]
        self._recommended_ids = set()
        groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for item in items:
            key = (
                str(item.get("page_url") or ""),
                str(item.get("title") or ""),
                int(item.get("tab_id") or 0),
            )
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            known = [
                item
                for item in group
                if str(item.get("quality_status") or "") == "ready"
                and int(item.get("height") or 0) > 0
            ]
            if known:
                best = max(known, key=capture_rank)
                best_id = str(best.get("id") or "")
                if best_id:
                    self._recommended_ids.add(best_id)

        for child in self.list_frame.winfo_children():
            child.destroy()

        if not items:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="ew", pady=75)
            ctk.CTkLabel(
                empty,
                text="No videos detected yet",
                text_color=TEXT,
                font=("Segoe UI Semibold", 16),
            ).pack()
            ctk.CTkLabel(
                empty,
                text="Keep Media Downloader open, play a video in the browser, and it will appear here.",
                text_color=MUTED,
                font=("Segoe UI", 10),
            ).pack(pady=(6, 0))
            return

        for row, item in enumerate(items):
            self._render_capture(row, item)

    def _render_capture(self, row: int, item: dict[str, Any]) -> None:
        capture_id = str(item.get("id") or "")
        kind = str(item.get("kind") or "unknown").upper()
        host = _host(str(item.get("url") or "")) or "Media host"
        page_host = _host(str(item.get("page_url") or ""))
        captured = _time_label(item.get("captured_at"))
        quality_status = str(item.get("quality_status") or "")
        quality_label = str(item.get("quality_label") or ("Checking…" if quality_status == "checking" else "Auto"))
        qualities = item.get("available_qualities")
        available_qualities = [str(value) for value in qualities] if isinstance(qualities, list) else []
        job = self.get_job_state(capture_id) or {}
        job_status = str(job.get("status") or "")
        progress = max(0.0, min(1.0, float(job.get("progress") or 0.0)))

        card = ctk.CTkFrame(
            self.list_frame,
            fg_color=SURFACE_2,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text=str(item.get("title") or "Detected video"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 12),
            anchor="w",
            justify="left",
            wraplength=730,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        source = page_host or host
        detail_parts = [source]
        if captured:
            detail_parts.append(captured)
        if available_qualities:
            detail_parts.append("Available: " + " / ".join(available_qualities[:6]))
        elif quality_status == "checking":
            detail_parts.append("Checking available quality…")
        ctk.CTkLabel(
            card,
            text=" • ".join(detail_parts),
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=14)

        quality_good = quality_status == "ready" and quality_label not in {"Unknown", "Auto"}
        is_recommended = capture_id in self._recommended_ids
        badge_text = quality_label
        if is_recommended and quality_good:
            badge_text = "BEST • " + quality_label.replace("Best ", "")
        ctk.CTkLabel(
            card,
            text=badge_text,
            width=84,
            height=26,
            corner_radius=8,
            fg_color="#241C52" if quality_good else SURFACE_3,
            text_color=TEXT if quality_good else MUTED,
            font=("Segoe UI Semibold", 8),
        ).grid(row=0, column=1, padx=(6, 4), pady=(10, 0))

        ctk.CTkLabel(
            card,
            text=kind,
            width=62,
            height=26,
            corner_radius=8,
            fg_color="#0E3025" if kind != "PAGE" else "#2D2514",
            text_color=SUCCESS if kind != "PAGE" else WARNING,
            font=("Segoe UI Semibold", 8),
        ).grid(row=0, column=2, padx=(4, 14), pady=(10, 0))

        action_row = 2
        if job_status:
            label_map = {
                "queued": f"Queued #{int(job.get('position') or 1)}",
                "running": f"Downloading {progress * 100:.0f}%",
                "completed": "Download completed",
                "failed": "Download failed",
                "cancelled": "Cancelled",
            }
            state_color = {
                "queued": WARNING,
                "running": CYAN,
                "completed": SUCCESS,
                "failed": DANGER,
                "cancelled": MUTED,
            }.get(job_status, MUTED)
            ctk.CTkLabel(
                card,
                text=label_map.get(job_status, job_status.title()),
                text_color=state_color,
                font=("Segoe UI Semibold", 9),
                anchor="w",
            ).grid(row=2, column=0, columnspan=3, sticky="w", padx=14, pady=(8, 2))
            action_row = 3
            if job_status == "running":
                progress_bar = ctk.CTkProgressBar(card, height=6, progress_color=PURPLE)
                progress_bar.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 4))
                progress_bar.set(progress)
                action_row = 4
                detail = str(job.get("detail") or "")
                if detail:
                    ctk.CTkLabel(
                        card,
                        text=detail,
                        text_color=MUTED,
                        font=("Segoe UI", 8),
                        anchor="w",
                    ).grid(row=4, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))
                    action_row = 5

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=action_row, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 12))

        if job_status in {"queued", "running"}:
            ctk.CTkButton(
                actions,
                text="Cancel",
                width=86,
                height=31,
                fg_color="transparent",
                hover_color="#30131B",
                border_width=1,
                border_color="#512332",
                text_color=DANGER,
                command=lambda capture_id=capture_id: self.on_cancel(capture_id),
            ).grid(row=0, column=0, padx=2)
        else:
            ctk.CTkButton(
                actions,
                text="Download best",
                width=108,
                height=31,
                fg_color=PURPLE,
                hover_color=PURPLE_HOVER,
                command=lambda item=item: self.on_download(dict(item), False),
            ).grid(row=0, column=0, padx=2)
            ctk.CTkButton(
                actions,
                text="Download & edit",
                width=116,
                height=31,
                fg_color=SURFACE_3,
                hover_color="#1B3153",
                border_width=1,
                border_color=BORDER,
                command=lambda item=item: self.on_download(dict(item), True),
            ).grid(row=0, column=1, padx=2)

        copy_col = 1 if job_status in {"queued", "running"} else 2
        ctk.CTkButton(
            actions,
            text="Copy URL",
            width=82,
            height=31,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            border_width=1,
            border_color=BORDER,
            command=lambda item=item: self.copy_url(item),
        ).grid(row=0, column=copy_col, padx=2)

        if job_status not in {"queued", "running"}:
            ctk.CTkButton(
                actions,
                text="Remove",
                width=76,
                height=31,
                fg_color="transparent",
                hover_color="#30131B",
                border_width=1,
                border_color="#512332",
                text_color=DANGER,
                command=lambda capture_id=capture_id: self.on_remove(capture_id),
            ).grid(row=0, column=copy_col + 1, padx=2)

    def copy_url(self, item: dict[str, Any]) -> None:
        url = str(item.get("url") or "")
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.connection_text.configure(text="Detected media URL copied.")
