"""Details for a failed (or auto-retrying) download: full error, editable link, retry."""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

BG = "#060B14"
SURFACE = "#0B1323"
SURFACE_3 = "#14223A"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
PURPLE = "#7657FF"
PURPLE_HOVER = "#6547E9"
DANGER = "#FF647C"


class FailureDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master: Any,
        item: dict[str, Any],
        *,
        on_retry: Callable[[str, str], None],
    ) -> None:
        super().__init__(master)
        self.title("Download details")
        self.geometry("720x520")
        self.minsize(560, 420)
        self.configure(fg_color=BG)
        self.transient(master)
        self._item = item
        self._on_retry = on_retry

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(self, text=str(item.get("name") or "Download"), text_color=TEXT,
                     font=("Segoe UI Semibold", 15), anchor="w").grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 2))
        attempts = int(item.get("auto_attempts") or 0)
        status = str(item.get("status") or "")
        sub = "Failed" if status == "failed" else f"Retrying automatically (attempt {attempts})" if attempts else status.title()
        ctk.CTkLabel(self, text=sub, text_color=DANGER if status == "failed" else MUTED,
                     font=("Segoe UI", 11), anchor="w").grid(row=1, column=0, sticky="ew", padx=20)

        link_row = ctk.CTkFrame(self, fg_color="transparent")
        link_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 8))
        link_row.grid_columnconfigure(0, weight=1)
        self.url_var = ctk.StringVar(value=str(item.get("url") or ""))
        ctk.CTkEntry(link_row, textvariable=self.url_var, height=36, fg_color=SURFACE_3,
                     border_color=BORDER).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(link_row, text="You can fix the link here and press “Retry with this link”.",
                     text_color=MUTED, font=("Segoe UI", 9), anchor="w").grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.box = ctk.CTkTextbox(self, fg_color=SURFACE, border_color=BORDER, border_width=1,
                                  text_color=TEXT, font=("Consolas", 11), wrap="word")
        self.box.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 10))
        error = str(item.get("last_error") or item.get("error") or "No error details were recorded.")
        self.box.insert("1.0", error)
        self.box.configure(state="disabled")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 18))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(buttons, text="Copy error", width=100, height=36, fg_color=SURFACE_3,
                      hover_color="#1B3153", command=self._copy).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(buttons, text="Close", width=80, height=36, fg_color=SURFACE_3,
                      hover_color="#1B3153", command=self.destroy).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(buttons, text="Retry with this link", width=160, height=36, fg_color=PURPLE,
                      hover_color=PURPLE_HOVER, command=self._retry).grid(row=0, column=3)
        self.after(100, self.focus_force)

    def _copy(self) -> None:
        text = f"{self.url_var.get()}\n\n{self._item.get('last_error') or self._item.get('error') or ''}"
        self.clipboard_clear()
        self.clipboard_append(text)

    def _retry(self) -> None:
        self._on_retry(str(self._item.get("id") or ""), self.url_var.get().strip())
        self.destroy()
