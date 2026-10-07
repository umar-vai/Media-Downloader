from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import customtkinter as ctk

from app_logging import log_path
from self_test import run_self_test
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


class DiagnosticsWindow(ctk.CTkToplevel):
    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master)
        self.title("Media Downloader Diagnostics")
        self.geometry("760x640")
        self.minsize(680, 520)
        self.configure(fg_color=BG)
        self.report: dict[str, Any] | None = None
        self.result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.checking = False
        self._closed = False
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_results()
        self._build_footer()

        self.after(100, self._poll_results)
        self.after(250, self.run_checks)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="SYSTEM DIAGNOSTICS",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))

        ctk.CTkLabel(
            header,
            text=f"Media Downloader v{APP_VERSION} • runtime health and support report",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

        self.summary_badge = ctk.CTkLabel(
            header,
            text="CHECKING",
            width=88,
            height=30,
            corner_radius=9,
            fg_color="#2D2514",
            text_color=WARNING,
            font=("Segoe UI Semibold", 9),
        )
        self.summary_badge.grid(row=0, column=1, rowspan=2, padx=20)

    def _build_results(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=16)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        self.summary_label = ctk.CTkLabel(
            body,
            text="Running runtime checks…",
            text_color=MUTED,
            font=("Segoe UI", 11),
            anchor="w",
            justify="left",
        )
        self.summary_label.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.results_frame = ctk.CTkScrollableFrame(
            body,
            fg_color=SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        self.results_frame.grid(row=1, column=0, sticky="nsew")
        self.results_frame.grid_columnconfigure(0, weight=1)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(3, weight=1)

        self.run_button = ctk.CTkButton(
            footer,
            text="Run checks",
            width=110,
            height=38,
            corner_radius=9,
            fg_color=PURPLE,
            hover_color="#6547E9",
            command=self.run_checks,
        )
        self.run_button.grid(row=0, column=0, padx=(18, 6), pady=14)

        self.copy_button = ctk.CTkButton(
            footer,
            text="Copy report",
            width=110,
            height=38,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.copy_report,
            state="disabled",
        )
        self.copy_button.grid(row=0, column=1, padx=6, pady=14)

        ctk.CTkButton(
            footer,
            text="Open log",
            width=100,
            height=38,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.open_log,
        ).grid(row=0, column=2, padx=6, pady=14)

        self.copy_status = ctk.CTkLabel(
            footer,
            text="",
            text_color=SUCCESS,
            font=("Segoe UI", 9),
            anchor="e",
        )
        self.copy_status.grid(row=0, column=3, sticky="e", padx=(6, 18))

    def run_checks(self) -> None:
        if self.checking:
            return
        self.checking = True
        self.run_button.configure(state="disabled", text="Checking…")
        self.copy_button.configure(state="disabled")
        self.copy_status.configure(text="")
        self.summary_badge.configure(text="CHECKING", fg_color="#2D2514", text_color=WARNING)
        self.summary_label.configure(text="Checking FFmpeg, mpv, updater, app mode and writable folders…")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        try:
            self.result_queue.put(run_self_test())
        except Exception as exc:
            self.result_queue.put(
                {
                    "ok": False,
                    "version": APP_VERSION,
                    "install_mode": "unknown",
                    "checks": [
                        {
                            "name": "diagnostics",
                            "ok": False,
                            "detail": str(exc) or exc.__class__.__name__,
                        }
                    ],
                }
            )

    def _poll_results(self) -> None:
        if self._closed:
            return
        try:
            while True:
                report = self.result_queue.get_nowait()
                self._apply_report(report)
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    def _apply_report(self, report: dict[str, Any]) -> None:
        self.report = report
        self.checking = False
        for child in self.results_frame.winfo_children():
            child.destroy()

        checks = list(report.get("checks") or [])
        failures = sum(1 for item in checks if not bool(item.get("ok")))
        install_mode = str(report.get("install_mode") or "unknown").capitalize()

        if bool(report.get("ok")):
            self.summary_badge.configure(text="HEALTHY", fg_color="#0E3025", text_color=SUCCESS)
            summary = f"All {len(checks)} checks passed • {install_mode} mode"
        else:
            self.summary_badge.configure(text="ATTENTION", fg_color="#351722", text_color=DANGER)
            summary = f"{failures} of {len(checks)} checks need attention • {install_mode} mode"

        self.summary_label.configure(text=summary)
        self.run_button.configure(state="normal", text="Run checks")
        self.copy_button.configure(state="normal")

        for row, item in enumerate(checks):
            ok = bool(item.get("ok"))
            card = ctk.CTkFrame(
                self.results_frame,
                fg_color=SURFACE_2,
                corner_radius=10,
                border_width=1,
                border_color=BORDER,
            )
            card.grid(row=row, column=0, sticky="ew", padx=6, pady=5)
            card.grid_columnconfigure(0, weight=1)

            name = str(item.get("name") or "check").replace("_", " ").title()
            ctk.CTkLabel(
                card,
                text=name,
                text_color=TEXT,
                font=("Segoe UI Semibold", 11),
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(9, 2))

            ctk.CTkLabel(
                card,
                text=str(item.get("detail") or ""),
                text_color=MUTED,
                font=("Segoe UI", 9),
                anchor="w",
                justify="left",
                wraplength=560,
            ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 9))

            ctk.CTkLabel(
                card,
                text="PASS" if ok else "FAIL",
                width=58,
                height=26,
                corner_radius=8,
                fg_color="#0E3025" if ok else "#351722",
                text_color=SUCCESS if ok else DANGER,
                font=("Segoe UI Semibold", 9),
            ).grid(row=0, column=1, rowspan=2, padx=12)

    def copy_report(self) -> None:
        if not self.report:
            return
        text = json.dumps(self.report, indent=2, ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_status.configure(text="Report copied")
        self.after(1800, lambda: self.copy_status.configure(text="") if not self._closed else None)

    def open_log(self) -> None:
        try:
            _open_path(log_path())
        except Exception as exc:
            self.copy_status.configure(text=f"Could not open log: {exc}")

    def _close(self) -> None:
        self._closed = True
        self.destroy()
