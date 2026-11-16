"""Ctrl+K command palette: type to filter actions, Enter to run.

A plain popup with a Windows edit box and list box in the system colours, like the quick
search boxes in classic Windows tools."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import native
from theme import C
from ui_kit import px


def filter_commands(commands: list[tuple[str, Callable[[], None]]], query: str) -> list[tuple[str, Callable[[], None]]]:
    words = [w for w in query.lower().split() if w]
    return [c for c in commands if all(w in c[0].lower() for w in words)]


class CommandPalette(tk.Toplevel):
    def __init__(self, master: Any, commands: list[tuple[str, Callable[[], None]]]) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        # A one-pixel window frame around the popup, as Windows draws for drop-down lists.
        self.configure(bg="SystemWindowFrame" if native.NATIVE else C["BORDER"])
        self._all = commands
        self._shown: list[tuple[str, Callable[[], None]]] = []

        inner = ttk.Frame(self, padding=(native.GAP, native.GAP, native.GAP, native.GAP))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(2, weight=1)
        ttk.Label(inner, text="Type a command (Enter runs it, Esc closes):").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        self.var = tk.StringVar(self)
        self.entry = ttk.Entry(inner, textvariable=self.var)
        self.entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, native.GAP))

        font = native.font(self)
        list_kw: dict[str, Any] = dict(activestyle="none", exportselection=False, font=font, height=12,
                                       relief="solid", borderwidth=1, highlightthickness=0)
        if native.NATIVE:
            list_kw.update(bg="SystemWindow", fg="SystemWindowText", selectbackground="SystemHighlight",
                           selectforeground="SystemHighlightText")
        else:
            list_kw.update(bg=C["SURFACE"], fg=C["TEXT"], selectbackground=C["SELECT"],
                           selectforeground=C["SELECT_TEXT"])
        self.listbox = tk.Listbox(inner, **list_kw)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")

        self.var.trace_add("write", lambda *_a: self._refresh())
        self.entry.bind("<Return>", lambda _e: self._run())
        self.entry.bind("<Down>", lambda _e: self._move(1))
        self.entry.bind("<Up>", lambda _e: self._move(-1))
        self.entry.bind("<Next>", lambda _e: self._move(10))
        self.entry.bind("<Prior>", lambda _e: self._move(-10))
        self.listbox.bind("<Return>", lambda _e: self._run())
        # One click runs a command, as before.
        self.listbox.bind("<ButtonRelease-1>", lambda e: self._run(self.listbox.nearest(e.y)))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<FocusOut>", self._focus_out)
        self._refresh()

        width = px(440)
        master.update_idletasks()
        self.update_idletasks()
        height = self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - width) // 2
        y = master.winfo_rooty() + px(80)
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.deiconify()
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
        self._shown = filter_commands(self._all, self.var.get())
        self.listbox.delete(0, "end")
        for label, _fn in self._shown:
            self.listbox.insert("end", f" {label}")
        if self._shown:
            self._select(0)
        else:
            self.listbox.insert("end", " No matching commands")
            self.listbox.itemconfigure(0, foreground=C["MUTED"])

    def _select(self, index: int) -> None:
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)

    def _current(self) -> int:
        selection = self.listbox.curselection()
        return int(selection[0]) if selection else 0

    def _move(self, step: int) -> str:
        if self._shown:
            self._select(max(0, min(len(self._shown) - 1, self._current() + step)))
        return "break"

    def _run(self, position: int | None = None) -> None:
        index = self._current() if position is None else position
        if not (0 <= index < len(self._shown)):
            return
        action = self._shown[index][1]
        self.destroy()
        self.master.after(30, action)
