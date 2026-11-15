"""Details for a failed (or auto-retrying) download: full error, editable link, retry."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import native
from native import GAP, PAD
from theme import C


class FailureDialog(native.Dialog):
    def __init__(self, master: Any, item: dict[str, Any], *, on_retry: Callable[[str, str], None]) -> None:
        super().__init__(master, "Download error", resizable=True)
        self._item = item
        self._on_retry = on_retry
        attempts = int(item.get("auto_attempts") or 0)
        status = str(item.get("status") or "")
        sub = ("The download failed." if status == "failed"
               else f"Retrying automatically (attempt {attempts})." if attempts else status.title())

        body = self.body
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(4, weight=1)
        ttk.Label(body, image="::tk::icons::error").grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, PAD))
        ttk.Label(body, text=str(item.get("name") or "Download"), style="Title.TLabel", wraplength=520).grid(
            row=0, column=1, sticky="w")
        ttk.Label(body, text=sub, style="Hint.TLabel").grid(row=1, column=1, sticky="w")

        ttk.Label(body, text="Address:").grid(row=2, column=0, sticky="w", pady=(PAD, 0))
        self.url_var = tk.StringVar(self, value=str(item.get("url") or ""))
        ttk.Entry(body, textvariable=self.url_var, width=70).grid(row=2, column=1, sticky="ew", pady=(PAD, 0))
        ttk.Label(body, text="Fix the address here if it changed, then press Retry.", style="Hint.TLabel").grid(
            row=3, column=1, sticky="w", pady=(2, GAP))

        box = native.group(body, "Error details", row=4, column=0, columnspan=2, sticky="nsew")
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.box = tk.Text(box, height=10, width=80, wrap="word", font=("Consolas", 9), relief="flat", bd=0,
                           highlightthickness=1, highlightbackground=C["BORDER"], bg=C["SURFACE"], fg=C["TEXT"])
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.box.yview)
        self.box.configure(yscrollcommand=scroll.set)
        self.box.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.box.insert("1.0", str(item.get("last_error") or item.get("error") or "No error details were recorded."))
        self.box.configure(state="disabled")

        self.add_button("Close", self.close)
        self.add_button("Copy error", self._copy)
        self.add_button("Retry", self._retry, default=True)
        self.show()

    def _copy(self) -> None:
        text = f"{self.url_var.get()}\n\n{self._item.get('last_error') or self._item.get('error') or ''}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text="Copied to the clipboard.")

    def _retry(self) -> None:
        self._on_retry(str(self._item.get("id") or ""), self.url_var.get().strip())
        self.close()
