"""Native Windows dialogs.

With the light theme every dialog uses Tk's ``vista`` theme, which draws the real Windows
controls (tabs, group boxes, check boxes, combo boxes, push buttons) in the system font, so
Options, Properties and the other windows look like any Windows program. The dark theme uses
the same widgets with dark colours.

Also provides small adapters (``Text``/``Button``/``Progress``) that accept the keyword
arguments the download engine code already uses (``text_color``, ``fg_color``, ``style``...).
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from theme import C, IS_DARK

NATIVE = os.name == "nt" and not IS_DARK
PAD = 10      # dialog margin (px at 96 dpi)
GAP = 6       # space between controls


def setup(root: tk.Misc) -> ttk.Style:
    """Choose the ttk theme for the whole app and define the shared dialog styles."""
    style = ttk.Style(root)
    names = style.theme_names()
    if NATIVE and "vista" in names:
        style.theme_use("vista")
    else:
        style.theme_use("clam")
        _dark_widgets(style)
    bg = dialog_bg(root)
    style.configure("Hint.TLabel", foreground=C["MUTED"])
    style.configure("Title.TLabel", font=_font(root, 2, bold=True))
    style.configure("Big.TLabel", font=_font(root, 5, bold=True))
    style.configure("Error.TLabel", foreground=C["DANGER"])
    style.configure("Ok.TLabel", foreground=C["SUCCESS"])
    style.configure("Link.TLabel", foreground=C["ACCENT"])
    style.configure("Sheet.TFrame", background=bg)
    return style


def _font(root: tk.Misc, bigger: int = 0, bold: bool = False) -> tuple:
    from tkinter import font as tkfont

    base = tkfont.nametofont("TkDefaultFont").actual()
    size = int(base.get("size") or 9)
    size = size + bigger if size > 0 else size - bigger
    return (base.get("family") or "Segoe UI", size, "bold" if bold else "normal")


def font(root: tk.Misc, bigger: int = 0, bold: bool = False) -> tuple:
    return _font(root, bigger, bold)


def dialog_bg(root: tk.Misc) -> str:
    if NATIVE:
        try:
            return ttk.Style(root).lookup("TFrame", "background") or "SystemButtonFace"
        except tk.TclError:
            return "SystemButtonFace"
    return C["BG"]


def _dark_widgets(style: ttk.Style) -> None:
    bg, field, text, border = C["BG"], C["SURFACE_2"], C["TEXT"], C["BORDER"]
    style.configure(".", background=bg, foreground=text, fieldbackground=field, bordercolor=border,
                    lightcolor=bg, darkcolor=bg, troughcolor=C["SURFACE"], selectbackground=C["ACCENT"],
                    selectforeground=C["ON_ACCENT"], insertcolor=text)
    style.map(".", foreground=[("disabled", C["FAINT"])])
    style.configure("TButton", background=C["SURFACE_3"], padding=(10, 3))
    style.map("TButton", background=[("pressed", C["BORDER"]), ("active", C["BORDER"])])
    style.configure("TEntry", fieldbackground=field)
    style.configure("TCombobox", fieldbackground=field, background=C["SURFACE_3"], arrowcolor=text)
    style.map("TCombobox", fieldbackground=[("readonly", field)], foreground=[("readonly", text)])
    style.configure("TSpinbox", fieldbackground=field, arrowcolor=text)
    style.configure("TCheckbutton", indicatorbackground=field, indicatorforeground=text)
    style.configure("TRadiobutton", indicatorbackground=field, indicatorforeground=text)
    style.configure("TNotebook", background=bg, bordercolor=border)
    style.configure("TNotebook.Tab", background=C["SURFACE"], padding=(10, 3))
    style.map("TNotebook.Tab", background=[("selected", bg)])
    style.configure("TLabelframe", background=bg, bordercolor=border)
    style.configure("TLabelframe.Label", background=bg, foreground=text)
    style.configure("Horizontal.TProgressbar", background=C["ACCENT"], troughcolor=C["SURFACE"])


# --------------------------------------------------------------------------- base dialog
class Dialog(tk.Toplevel):
    """A Windows-style dialog: fixed margins, buttons bottom-right, Esc/Enter, centred on its owner."""

    def __init__(self, master: tk.Misc, title: str, *, resizable: bool = False, modal: bool = False,
                 icon: str | None = None) -> None:
        super().__init__(master)
        self.withdraw()
        self.title(title)
        self.configure(bg=dialog_bg(self))
        self.transient(master.winfo_toplevel())
        self.resizable(resizable, resizable)
        if icon:
            try:
                self.iconbitmap(icon)
            except tk.TclError:
                pass
        self._modal = modal
        self._placed = False
        self.body = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        self.body.pack(side="top", fill="both", expand=True)
        self.buttons = ttk.Frame(self, padding=(PAD, PAD + 2, PAD, PAD))
        self.buttons.pack(side="bottom", fill="x")
        self.status = ttk.Label(self.buttons, text="", style="Hint.TLabel")
        self.status.pack(side="left", fill="x", expand=True)
        self._button_widgets: list[ttk.Button] = []
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())

    def add_button(self, text: str, command: Callable[[], Any], *, default: bool = False,
                   width: int = 11) -> ttk.Button:
        button = ttk.Button(self.buttons, text=text, command=command, width=width,
                            default="active" if default else "normal")
        button.pack(side="right", padx=(GAP, 0))
        self._button_widgets.append(button)
        if default:
            self.bind("<Return>", lambda e: None if isinstance(e.widget, tk.Text) else command())
        return button

    def show(self) -> None:
        if not self._placed:
            self._placed = True
            self.update_idletasks()
            owner = self.master.winfo_toplevel()
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            try:
                x = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
                y = owner.winfo_rooty() + max(0, (owner.winfo_height() - h) // 3)
            except tk.TclError:
                x = y = 120
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if self._modal:
            self.grab_set()

    @property
    def is_open(self) -> bool:
        try:
            return self.winfo_exists() and self.state() != "withdrawn"
        except tk.TclError:
            return False

    def cancel(self) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class HiddenDialog(Dialog):
    """A dialog that is hidden instead of destroyed, so the app can keep updating it."""

    on_close: Callable[[], None] | None = None

    def open(self) -> None:
        self.show()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.withdraw()
        if self.on_close:
            self.on_close()
        try:
            self.master.focus_force()
        except tk.TclError:
            pass


def group(parent: tk.Misc, title: str, **grid: Any) -> ttk.Labelframe:
    frame = ttk.Labelframe(parent, text=f" {title} ", padding=(PAD, 4, PAD, PAD))
    if grid:
        frame.grid(**grid)
    return frame


def check(parent: tk.Misc, text: str, var: tk.Variable, row: int, *, column: int = 0, columnspan: int = 3,
          pady: int = 2) -> ttk.Checkbutton:
    box = ttk.Checkbutton(parent, text=text, variable=var)
    box.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=pady)
    return box


def combo(parent: tk.Misc, var: tk.Variable, values: list[str], width: int = 16) -> ttk.Combobox:
    return ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)


# --------------------------------------------------------------------------- adapters
_COLOR_KEYS = ("text_color", "fg")


class Text(ttk.Label):
    """ttk.Label that also accepts the canvas-kit keywords used elsewhere in the app."""

    def __init__(self, master: tk.Misc, text: str = "", *, wrap: int = 0, **kw: Any) -> None:
        super().__init__(master, text=text, **kw)
        self._wrap = wrap
        if wrap:
            self.configure(wraplength=wrap)
            self.bind("<Configure>", self._rewrap, add="+")

    def _rewrap(self, event: Any) -> None:
        if self._wrap and event.width > 40:
            super().configure(wraplength=event.width - 4)

    def configure(self, cnf: Any = None, **kw: Any) -> Any:
        color = None
        for key in _COLOR_KEYS:
            if key in kw:
                color = kw.pop(key)
        kw.pop("fg_color", None)
        if color:
            kw["foreground"] = color
        if cnf is None and not kw:
            return super().configure()
        return super().configure(cnf, **kw)

    config = configure

    @property
    def text(self) -> str:
        return str(self.cget("text"))

    def show(self, on: bool = True) -> None:
        if on:
            self.grid()
        else:
            self.grid_remove()


class Button(ttk.Button):
    """ttk.Button that ignores colour/style keywords and keeps ``.state``/``.text`` readable."""

    def configure(self, cnf: Any = None, **kw: Any) -> Any:
        for key in ("style", "fg_color", "text_color"):
            kw.pop(key, None)
        if cnf is None and not kw:
            return super().configure()
        return super().configure(cnf, **kw)

    config = configure

    @property
    def text(self) -> str:
        return str(self.cget("text"))



class Progress(ttk.Progressbar):
    """ttk.Progressbar with the ``set(fraction)`` call the update code uses."""

    def __init__(self, master: tk.Misc, **kw: Any) -> None:
        super().__init__(master, orient="horizontal", mode="determinate", maximum=1000, **kw)

    def set(self, value: float) -> None:  # type: ignore[override]
        self.configure(value=max(0.0, min(1.0, float(value))) * 1000)
