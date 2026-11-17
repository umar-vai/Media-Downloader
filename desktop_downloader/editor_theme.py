"""Dark "pro editor" look for the media editor window only.

Video editors (Premiere Pro, DaVinci Resolve, CapCut) are dark so the picture stands out.
The rest of the app keeps its own (light, Windows-like) theme, so this cannot switch the
global ttk theme. Instead every ttk widget in the editor gets a ``Pro.*`` style whose
layout is built from the ``clam`` theme's elements (copied into whatever theme is active:
the Windows ``vista`` theme draws native controls that cannot be recoloured).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

PALETTE = {
    "bg": "#16171B",          # window
    "panel": "#1E1F24",       # side panel, tool bars
    "field": "#2A2C33",       # entries, combo boxes, buttons
    "hover": "#363943",
    "pressed": "#43475A",
    "border": "#34363F",
    "text": "#E6E8EE",
    "muted": "#9095A3",
    "disabled": "#5E6270",
    "accent": "#4C8DFF",
    "accent_hover": "#6AA1FF",
    "accent_text": "#FFFFFF",
    "danger": "#FF6B6B",
    "warning": "#F5C542",
    "success": "#4ADE80",
    "preview": "#000000",
}

PREFIX = "Pro."
_BASE_CLASSES = ("TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TEntry", "TSpinbox", "TLabelframe",
                 "TNotebook", "TNotebook.Tab", "Horizontal.TScale", "Vertical.TScale", "Horizontal.TProgressbar",
                 "Toolbutton", "TFrame", "TLabel", "TSeparator", "Vertical.TScrollbar", "Horizontal.TScrollbar")
_ready: set[str] = set()


def _clam_layout(style: ttk.Style, name: str) -> Any:
    """The clam theme's layout of ``name``, read without switching the app's theme."""
    style.tk.call("ttk::style", "theme", "settings", "clam",
                  f"set ::_pro_layout [ttk::style layout {name}]")
    return style.tk.call("set", "::_pro_layout")


def _copy_elements(style: ttk.Style, spec: Any, created: set[str]) -> Any:
    """Rename every element in a layout spec to its Pro. copy (creating it from clam)."""
    # The spec is flat: "Element -opt value -children {...} Sibling -opt value ...".
    items = list(style.tk.splitlist(spec))
    result: list[Any] = []
    index = 0
    while index < len(items):
        token = str(items[index])
        if token.startswith("-") and index + 1 < len(items):
            value = items[index + 1]
            if token == "-children":
                value = _copy_elements(style, value, created)
            result += [token, value]
            index += 2
            continue
        new_name = PREFIX + token
        if new_name not in created:
            try:
                style.element_create(new_name, "from", "clam", token)
            except tk.TclError:
                pass  # already exists in this theme
            created.add(new_name)
        result.append(new_name)
        index += 1
    return tuple(result)


def setup(root: tk.Misc) -> ttk.Style:
    """Create the Pro.* styles once per ttk theme."""
    style = ttk.Style(root)
    theme = style.theme_use()
    p = PALETTE
    if theme not in _ready:
        created: set[str] = set()
        for name in _BASE_CLASSES:
            try:
                spec = _clam_layout(style, name)
            except tk.TclError:
                continue
            if not spec:
                continue
            try:
                style.tk.call("ttk::style", "layout", PREFIX + name, _copy_elements(style, spec, created))
            except tk.TclError:
                pass
        _ready.add(theme)

    common = {"background": p["panel"], "foreground": p["text"], "bordercolor": p["border"],
              "lightcolor": p["panel"], "darkcolor": p["panel"], "troughcolor": p["bg"],
              "fieldbackground": p["field"], "selectbackground": p["accent"], "selectforeground": p["accent_text"],
              "insertcolor": p["text"], "arrowcolor": p["text"], "focuscolor": p["accent"],
              "indicatorbackground": p["field"], "indicatorforeground": p["text"], "font": "TkDefaultFont"}
    for name in _BASE_CLASSES:
        style.configure(PREFIX + name, **common)
    style.configure("Pro.TFrame", background=p["panel"])
    style.configure("Pro.Dark.TFrame", background=p["bg"])
    style.configure("Pro.TLabel", background=p["panel"], foreground=p["text"])
    style.configure("Pro.TSeparator", background=p["border"])
    style.configure("Pro.TLabelframe", background=p["panel"], bordercolor=p["border"], relief="solid",
                    borderwidth=1, lightcolor=p["border"], darkcolor=p["border"])
    style.configure("Pro.TLabelframe.Label", background=p["panel"], foreground=p["muted"])

    button = {"background": p["field"], "foreground": p["text"], "bordercolor": p["border"],
              "lightcolor": p["field"], "darkcolor": p["field"], "padding": (10, 4), "relief": "flat"}
    for name in ("Pro.TButton", "Pro.Toolbutton"):
        style.configure(name, **button)
        style.map(name,
                  background=[("disabled", p["panel"]), ("pressed", p["pressed"]), ("selected", p["accent"]),
                              ("active", p["hover"])],
                  foreground=[("disabled", p["disabled"]), ("selected", p["accent_text"])],
                  lightcolor=[("pressed", p["pressed"]), ("selected", p["accent"]), ("active", p["hover"])],
                  darkcolor=[("pressed", p["pressed"]), ("selected", p["accent"]), ("active", p["hover"])],
                  bordercolor=[("focus", p["accent"]), ("selected", p["accent"])])
    style.configure("Pro.Accent.TButton", **{**button, "background": p["accent"], "lightcolor": p["accent"],
                                              "darkcolor": p["accent"], "bordercolor": p["accent"],
                                              "foreground": p["accent_text"]})
    style.map("Pro.Accent.TButton",
              background=[("disabled", p["panel"]), ("pressed", p["accent"]), ("active", p["accent_hover"])],
              foreground=[("disabled", p["disabled"])],
              lightcolor=[("active", p["accent_hover"])], darkcolor=[("active", p["accent_hover"])])

    for name in ("Pro.TCheckbutton", "Pro.TRadiobutton"):
        style.configure(name, background=p["panel"], foreground=p["text"], indicatorbackground=p["field"],
                        indicatorforeground=p["text"])
        style.map(name, background=[("active", p["panel"])], foreground=[("disabled", p["disabled"])],
                  indicatorbackground=[("selected", p["accent"]), ("pressed", p["hover"])],
                  indicatorforeground=[("selected", p["accent_text"])])

    for name in ("Pro.TEntry", "Pro.TCombobox", "Pro.TSpinbox"):
        style.configure(name, fieldbackground=p["field"], foreground=p["text"], background=p["field"],
                        bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                        arrowcolor=p["text"], insertcolor=p["text"], padding=(4, 2))
        style.map(name, fieldbackground=[("readonly", p["field"]), ("disabled", p["panel"])],
                  foreground=[("disabled", p["disabled"]), ("readonly", p["text"])],
                  bordercolor=[("focus", p["accent"]), ("hover", p["hover"])],
                  background=[("active", p["hover"]), ("pressed", p["pressed"])],
                  selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["text"])])

    style.configure("Pro.TNotebook", background=p["panel"], bordercolor=p["border"], lightcolor=p["panel"],
                    darkcolor=p["panel"], tabmargins=(0, 0, 0, 0))
    style.configure("Pro.TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(14, 5),
                    bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"])
    style.map("Pro.TNotebook.Tab", background=[("selected", p["panel"]), ("active", p["hover"])],
              foreground=[("selected", p["text"]), ("active", p["text"])],
              lightcolor=[("selected", p["accent"])])

    for name in ("Pro.Horizontal.TScale", "Pro.Vertical.TScale"):
        style.configure(name, background=p["accent"], troughcolor=p["field"], bordercolor=p["border"],
                        lightcolor=p["accent"], darkcolor=p["accent"])
        style.map(name, background=[("active", p["accent_hover"])])
    style.configure("Pro.Horizontal.TProgressbar", background=p["accent"], troughcolor=p["field"],
                    bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])
    for name in ("Pro.Vertical.TScrollbar", "Pro.Horizontal.TScrollbar"):
        style.configure(name, background=p["field"], troughcolor=p["bg"], arrowcolor=p["text"])
    return style


# Light-theme colours that may have been set directly on a label: map them to dark ones.
_TEXT_MAP_KEYS = ("#000000", "black", "#444444", "#6d6d6d", "systemwindowtext", "systembuttontext")


def _style_for(widget: tk.Misc) -> str | None:
    cls = widget.winfo_class()
    try:
        current = str(widget.cget("style"))
    except tk.TclError:
        return None
    if current.startswith(PREFIX):
        return None
    if current == "Toolbutton":
        return "Pro.Toolbutton"
    if cls in {"TScale", "TProgressbar", "TScrollbar"}:
        orient = str(widget.cget("orient")) or "horizontal"
        return f"{PREFIX}{orient.capitalize()}.{cls}"
    if cls == "TButton" and str(widget.cget("default")) == "active":
        return "Pro.Accent.TButton"
    if cls in {"TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TEntry", "TSpinbox", "TLabelframe",
               "TNotebook", "TFrame", "TLabel", "TSeparator"}:
        return PREFIX + cls
    return None


def apply(window: tk.Misc) -> None:
    """Style every widget of ``window`` (call again after adding widgets)."""
    setup(window)
    p = PALETTE
    try:
        window.configure(bg=p["bg"])
    except tk.TclError:
        pass
    pattern = f"*{window.winfo_class()}*"
    for key, value in (("Listbox.background", p["field"]), ("Listbox.foreground", p["text"]),
                       ("Listbox.selectBackground", p["accent"]), ("Listbox.selectForeground", p["accent_text"]),
                       ("Menu.background", p["panel"]), ("Menu.foreground", p["text"]),
                       ("Menu.activeBackground", p["accent"]), ("Menu.activeForeground", p["accent_text"])):
        window.option_add(pattern + key, value)
    _walk(window)


def _walk(widget: tk.Misc) -> None:
    p = PALETTE
    for child in widget.winfo_children():
        name = _style_for(child)
        if name:
            try:
                child.configure(style=name)
            except tk.TclError:
                pass
        cls = child.winfo_class()
        if cls == "TLabel":
            try:
                color = str(child.cget("foreground")).lower()
                if color in _TEXT_MAP_KEYS:
                    child.configure(foreground=p["muted"] if color in {"#444444", "#6d6d6d"} else p["text"])
            except tk.TclError:
                pass
        elif cls == "Frame":
            try:
                if str(child.cget("bg")).lower() not in {p["preview"], "#000000", "black"}:
                    child.configure(bg=p["panel"])
            except tk.TclError:
                pass
        elif cls == "Menu":
            try:
                child.configure(bg=p["panel"], fg=p["text"], activebackground=p["accent"],
                                activeforeground=p["accent_text"], bd=0)
            except tk.TclError:
                pass
        _walk(child)
