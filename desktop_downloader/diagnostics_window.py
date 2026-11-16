"""Help > Diagnostics: runs the self test and shows each check in a plain Windows list."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

import native
from app_logging import log_path
from self_test import run_self_test
from theme import C
from ui_kit import px
from version import APP_VERSION


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def check_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or "check").replace("_", " ").title()


class DiagnosticsWindow(native.Dialog):
    """A classic dialog: a one-line summary, a Check / Result / Details list, the full detail
    of the selected check underneath, and push buttons at the bottom right."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, "Diagnostics", resizable=True)
        self.report: dict[str, Any] | None = None
        self.result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.checking = False
        self._closed = False
        self._details: dict[str, str] = {}

        body = self.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        self.summary_label = ttk.Label(body, text="Running checks…", style="Title.TLabel")
        self.summary_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, native.GAP))

        # Same list style as the main download list (row height scales with the display).
        self.tree = ttk.Treeview(body, columns=("check", "result", "detail"), show="headings", height=10,
                                 selectmode="browse", style="MD.Treeview")
        self.tree.heading("check", text="Check", anchor="w")
        self.tree.heading("result", text="Result", anchor="w")
        self.tree.heading("detail", text="Details", anchor="w")
        self.tree.column("check", width=px(140), stretch=False)
        self.tree.column("result", width=px(64), stretch=False)
        self.tree.column("detail", width=px(440), stretch=True)
        self.tree.tag_configure("fail", foreground=C["DANGER"])
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

        self.detail_label = ttk.Label(body, text="", wraplength=px(600), justify="left")
        self.detail_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(native.GAP, 0))
        body.bind("<Configure>", lambda e: self.detail_label.configure(wraplength=max(200, e.width - 8)), add="+")

        self.add_button("Close", self._close, default=True)
        self.add_button("Open log", self.open_log)
        self.copy_button = self.add_button("Copy report", self.copy_report)
        self.run_button = self.add_button("Run checks", self.run_checks)
        self.copy_button.configure(state="disabled")
        self.copy_status = self.status
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.minsize(px(480), px(300))
        self.show()

        self.after(100, self._poll_results)
        self.after(250, self.run_checks)

    def cancel(self) -> None:  # Esc
        self._close()

    def run_checks(self) -> None:
        if self.checking:
            return
        self.checking = True
        self.run_button.configure(state="disabled", text="Checking…")
        self.copy_button.configure(state="disabled")
        self.copy_status.configure(text="")
        self.summary_label.configure(text="Checking FFmpeg, mpv, updater, app mode and writable folders…",
                                     foreground=C["TEXT"])
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
        self.tree.delete(*self.tree.get_children())
        self._details.clear()

        checks = list(report.get("checks") or [])
        failures = sum(1 for item in checks if not bool(item.get("ok")))
        install_mode = str(report.get("install_mode") or "unknown").capitalize()
        if bool(report.get("ok")):
            summary = f"All {len(checks)} checks passed. {install_mode} installation, version {APP_VERSION}."
            color = C["TEXT"]
        else:
            summary = (f"{failures} of {len(checks)} checks failed. {install_mode} installation, "
                       f"version {APP_VERSION}.")
            color = C["DANGER"]
        self.summary_label.configure(text=summary, foreground=color)
        self.run_button.configure(state="normal", text="Run checks")
        self.copy_button.configure(state="normal")

        first_fail = ""
        for index, item in enumerate(checks):
            ok = bool(item.get("ok"))
            detail = " ".join(str(item.get("detail") or "").split())
            iid = f"c{index}"
            self.tree.insert("", "end", iid=iid, values=(check_name(item), "OK" if ok else "Failed", detail),
                             tags=() if ok else ("fail",))
            self._details[iid] = f"{check_name(item)}: {item.get('detail') or ''}".strip()
            if not ok and not first_fail:
                first_fail = iid
        target = first_fail or (self.tree.get_children() or [""])[0]
        if target:
            self.tree.selection_set(target)
            self.tree.see(target)
        self._show_detail()

    def _show_detail(self) -> None:
        selection = self.tree.selection()
        self.detail_label.configure(text=self._details.get(selection[0], "") if selection else "")

    def copy_report(self) -> None:
        if not self.report:
            return
        text = json.dumps(self.report, indent=2, ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_status.configure(text="Report copied to the clipboard.")
        self.after(2500, lambda: self.copy_status.configure(text="") if not self._closed else None)

    def open_log(self) -> None:
        try:
            _open_path(log_path())
        except Exception as exc:
            self.copy_status.configure(text=f"Could not open the log: {exc}")

    def _close(self) -> None:
        self._closed = True
        self.close()
