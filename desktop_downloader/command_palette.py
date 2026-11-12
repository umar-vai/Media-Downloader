"""Ctrl+K command palette: type to filter actions, Enter to run."""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

BG = "#060B14"
SURFACE_2 = "#101C31"
SURFACE_3 = "#14223A"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
PURPLE = "#7657FF"


def filter_commands(commands: list[tuple[str, Callable[[], None]]], query: str) -> list[tuple[str, Callable[[], None]]]:
    words = [w for w in query.lower().split() if w]
    return [c for c in commands if all(w in c[0].lower() for w in words)]


class CommandPalette(ctk.CTkToplevel):
    def __init__(self, master: Any, commands: list[tuple[str, Callable[[], None]]]) -> None:
        super().__init__(master)
        self.overrideredirect(True)
        self.configure(fg_color=BORDER)
        self._all = commands
        self._shown: list[tuple[str, Callable[[], None]]] = []
        self._index = 0

        width, height = 520, 360
        master.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - width) // 2
        y = master.winfo_rooty() + 110
        self.geometry(f"{width}x{height}+{x}+{y}")

        inner = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.var = ctk.StringVar()
        self.entry = ctk.CTkEntry(inner, textvariable=self.var, height=44, fg_color=SURFACE_2, border_width=0,
                                  text_color=TEXT, placeholder_text="Type a command…  (Esc to close)",
                                  font=("Segoe UI", 14))
        self.entry.pack(fill="x", padx=10, pady=10)
        self.list_frame = ctk.CTkScrollableFrame(inner, fg_color=BG)
        self.list_frame.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.var.trace_add("write", lambda *_a: self._refresh())
        self.entry.bind("<Return>", lambda _e: self._run())
        self.entry.bind("<Down>", lambda _e: self._move(1))
        self.entry.bind("<Up>", lambda _e: self._move(-1))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.entry.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<FocusOut>", self._focus_out)
        self._refresh()
        self.after(30, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.entry.focus_set()
        except Exception:
            pass

    def _focus_out(self, _event: Any) -> None:
        self.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self) -> None:
        try:
            if self.winfo_exists() and self.focus_displayof() is None:
                self.destroy()
        except Exception:
            pass

    def _refresh(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._shown = filter_commands(self._all, self.var.get())
        self._index = 0
        for position, (label, _fn) in enumerate(self._shown[:12]):
            row = ctk.CTkButton(
                self.list_frame, text=label, anchor="w", height=34, corner_radius=8,
                fg_color=PURPLE if position == self._index else "transparent",
                hover_color=SURFACE_3, text_color=TEXT, font=("Segoe UI", 12),
                command=lambda p=position: self._run(p),
            )
            row.pack(fill="x", pady=1)
        if not self._shown:
            ctk.CTkLabel(self.list_frame, text="No matching commands", text_color=MUTED).pack(pady=20)

    def _move(self, step: int) -> str:
        if not self._shown:
            return "break"
        self._index = (self._index + step) % min(len(self._shown), 12)
        for position, child in enumerate(self.list_frame.winfo_children()):
            if isinstance(child, ctk.CTkButton):
                child.configure(fg_color=PURPLE if position == self._index else "transparent")
        return "break"

    def _run(self, position: int | None = None) -> None:
        index = self._index if position is None else position
        if not (0 <= index < len(self._shown)):
            return
        action = self._shown[index][1]
        self.destroy()
        self.master.after(30, action)
