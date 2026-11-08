"""Canvas-drawn UI toolkit for Media Downloader.

Why this exists
---------------
On Windows every Tk widget is a separate native child window, and repainting one
costs roughly half a millisecond even when it is an empty frame.  A screen with a
couple of hundred widgets (CustomTkinter makes it worse by drawing each rounded
widget on its own canvas) therefore redraws at ~5-10 fps while the window edge is
dragged.

Here every page is a *single* canvas.  Cards, buttons, chips, progress bars,
labels and list rows are canvas items (cheap vector shapes), and only the few
elements that truly need native editing (text boxes) are real widgets.  A page is
re-laid-out with ``coords()`` calls on resize, which keeps dragging smooth.

All sizes passed to ``place`` are device pixels; call ``px()`` for design values.
"""

from __future__ import annotations

import itertools
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
BG = "#080B12"
SIDEBAR = "#0B0F18"
SURFACE = "#111623"
SURFACE_2 = "#171E2E"
SURFACE_3 = "#1E2740"
BORDER = "#252F47"
BORDER_SOFT = "#1B2336"
TEXT = "#EEF2FF"
MUTED = "#8A94AE"
FAINT = "#5A647E"
ACCENT = "#7C6CFF"
ACCENT_HOVER = "#6A59F0"
ACCENT_SOFT = "#241F4D"
CYAN = "#2DD4FF"
SUCCESS = "#34D399"
SUCCESS_SOFT = "#0F2E26"
WARNING = "#FBBF24"
WARNING_SOFT = "#33290F"
DANGER = "#FB7185"
DANGER_SOFT = "#361721"
INFO_SOFT = "#13233F"

FONT_FAMILY = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"
ICON_FAMILY = "Segoe MDL2 Assets"

SCALE = 1.0
_fonts: dict[tuple, tkfont.Font] = {}
_uid = itertools.count(1)


def init(root: tk.Misc) -> None:
    global SCALE
    try:
        SCALE = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except Exception:
        SCALE = 1.0


def px(value: float) -> int:
    return int(round(value * SCALE))


def font(size: int = 12, bold: bool = False, family: str | None = None) -> tkfont.Font:
    fam = family or (FONT_BOLD if bold else FONT_FAMILY)
    key = (fam, size)
    f = _fonts.get(key)
    if f is None:
        f = tkfont.Font(family=fam, size=-px(size))
        _fonts[key] = f
    return f


def icon_available(root: tk.Misc) -> bool:
    try:
        return ICON_FAMILY in set(tkfont.families(root))
    except Exception:
        return False


def rrect(x0: float, y0: float, x1: float, y1: float, r: float) -> list[float]:
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


_ell_cache: dict[tuple, str] = {}


def ellipsize(text: str, fnt: tkfont.Font, width: float) -> str:
    if width <= 4 or not text:
        return ""
    key = (text, id(fnt), int(width))
    hit = _ell_cache.get(key)
    if hit is not None:
        return hit
    if fnt.measure(text) <= width:
        out = text
    else:
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fnt.measure(text[:mid].rstrip() + "…") <= width:
                lo = mid
            else:
                hi = mid - 1
        out = text[:lo].rstrip() + "…"
    if len(_ell_cache) > 4000:
        _ell_cache.clear()
    _ell_cache[key] = out
    return out


# --------------------------------------------------------------------------- #
# Page: one canvas per screen, with a layout callback and a built-in scrollbar
# --------------------------------------------------------------------------- #
class Page(tk.Canvas):
    def __init__(self, master: tk.Misc, layout: Callable[[int, int], int] | None = None, *,
                 bg: str = BG, scroll: bool = True) -> None:
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.layout_fn = layout
        self.elements: list[El] = []
        self.vw = 1
        self.vh = 1
        self.content_h = 0
        self._layout_job: str | None = None
        self._scroll = scroll
        self._anim: set[Bar] = set()
        self._anim_job: str | None = None
        self._sb_frac = (0.0, 1.0)
        self._sb_grab = 0.0
        self.bind("<Configure>", self._on_configure)
        if scroll:
            self.configure(yscrollcommand=self._on_yscroll)
            self.create_line(0, 0, 0, 0, width=px(6), capstyle="round", fill=SURFACE_3,
                             tags=("sb",), state="hidden")
            self.tag_bind("sb", "<Enter>", lambda _e: self.itemconfigure("sb", fill=FAINT))
            self.tag_bind("sb", "<Leave>", lambda _e: self.itemconfigure("sb", fill=SURFACE_3))
            self.tag_bind("sb", "<ButtonPress-1>", self._sb_press)
            self.tag_bind("sb", "<B1-Motion>", self._sb_drag)

    # layout -------------------------------------------------------------
    def request_layout(self) -> None:
        if self._layout_job is None:
            self._layout_job = self.after_idle(self.relayout)

    def _on_configure(self, event: Any) -> None:
        self.vw, self.vh = max(1, event.width), max(1, event.height)
        self.request_layout()

    def relayout(self) -> None:
        self._layout_job = None
        if self.layout_fn is None or not self.winfo_exists():
            return
        height = int(self.layout_fn(self.vw, self.vh))
        self.content_h = height
        self.configure(scrollregion=(0, 0, self.vw, max(self.vh, height)))
        if height <= self.vh:
            self.yview_moveto(0)
        self._draw_scrollbar()
        if self._scroll:
            self.tag_raise("sb")

    # scrolling ----------------------------------------------------------
    def _on_yscroll(self, first: str, last: str) -> None:
        self._sb_frac = (float(first), float(last))
        self._draw_scrollbar()

    def _draw_scrollbar(self) -> None:
        if not self._scroll:
            return
        first, last = self._sb_frac
        if last - first >= 0.999 or self.content_h <= self.vh:
            self.itemconfigure("sb", state="hidden")
            return
        off = self.canvasy(0)
        top = off + self.vh * first + px(3)
        bottom = max(top + px(30), off + self.vh * last - px(3))
        x = self.vw - px(7)
        self.coords("sb", x, top, x, bottom)
        self.itemconfigure("sb", state="normal")

    def _sb_press(self, event: Any) -> None:
        self._sb_grab = event.y - self.vh * self._sb_frac[0]

    def _sb_drag(self, event: Any) -> None:
        self.yview_moveto(max(0.0, (event.y - self._sb_grab) / max(1, self.vh)))

    def wheel(self, delta: int) -> None:
        if self.content_h > self.vh:
            self.yview_scroll(int(-delta / 120 * 48), "units")

    # progress-bar animation (one timer per page, only while something moves) ------
    def animate(self, bar: "Bar") -> None:
        self._anim.add(bar)
        if self._anim_job is None:
            self._anim_job = self.after(16, self._tick)

    def _tick(self) -> None:
        self._anim_job = None
        if not self.winfo_exists():
            return
        visible = self.winfo_ismapped()
        keep = {bar for bar in list(self._anim) if bar.step(visible)}
        self._anim = keep
        if keep:
            self._anim_job = self.after(16 if visible else 300, self._tick)


def install_wheel(root: tk.Misc) -> None:
    def on_wheel(event: Any) -> None:
        try:
            widget = root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        if isinstance(widget, tk.Text):
            return
        while widget is not None and not isinstance(widget, Page):
            widget = getattr(widget, "master", None)
        if isinstance(widget, Page):
            widget.wheel(event.delta)

    root.bind_all("<MouseWheel>", on_wheel)


# --------------------------------------------------------------------------- #
# Elements
# --------------------------------------------------------------------------- #
class El:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.tag = f"e{next(_uid)}"
        self.visible = True
        self._geo: tuple | None = None
        page.elements.append(self)

    def _poly(self, **kw: Any) -> int:
        return self.page.create_polygon(0, 0, 0, 0, 0, 0, smooth=True, splinesteps=8, tags=(self.tag,), **kw)

    def show(self, on: bool = True) -> None:
        if on != self.visible:
            self.visible = on
            self.page.itemconfigure(self.tag, state="normal" if on else "hidden")
            self._geo = None
            self.page.request_layout()

    # CustomTkinter-style names so existing handlers keep working
    def grid(self, *_a: Any, **_k: Any) -> None:
        self.show(True)

    def grid_remove(self, *_a: Any, **_k: Any) -> None:
        self.show(False)

    def destroy(self) -> None:
        self.page.delete(self.tag)
        if self in self.page.elements:
            self.page.elements.remove(self)

    def _same(self, *geo: Any) -> bool:
        if self._geo == geo:
            return True
        self._geo = geo
        return False

    def relayout(self) -> None:
        self._geo = None
        self.page.request_layout()


class Card(El):
    def __init__(self, page: Page, *, fill: str = SURFACE, outline: str = BORDER_SOFT, radius: int = 14) -> None:
        super().__init__(page)
        self.radius = px(radius)
        self.poly = self._poly(fill=fill, outline=outline, width=1)

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, self.radius))


class Label(El):
    """Canvas text. ``wrap`` wraps to the placed width, ``ellipsis`` truncates to it."""

    def __init__(self, page: Page, text: str = "", *, fg: str = TEXT, size: int = 12, bold: bool = False,
                 anchor: str = "nw", wrap: bool = False, ellipsis: bool = False, relayout: bool = True) -> None:
        super().__init__(page)
        self.text = text
        self.fnt = font(size, bold)
        self.wrap = wrap
        self.ellipsis = ellipsis
        self.affects_layout = relayout
        self.id = page.create_text(0, 0, text=text, fill=fg, font=self.fnt, anchor=anchor, tags=(self.tag,), width=0)
        self._w = 0.0
        self._h = float(self.fnt.metrics("linespace"))

    def configure(self, cnf: Any = None, **kw: Any) -> None:
        if "text" in kw and str(kw["text"]) != self.text:
            self.text = str(kw["text"])
            self._apply_text()
            if self.affects_layout:
                self.relayout()
        color = kw.get("text_color", kw.get("fg"))
        if color:
            self.page.itemconfigure(self.id, fill=color)

    config = configure

    def _apply_text(self) -> None:
        shown = ellipsize(self.text, self.fnt, self._w) if self.ellipsis and self._w else self.text
        self.page.itemconfigure(self.id, text=shown)

    def width_of(self) -> int:
        return self.fnt.measure(self.text)

    def place(self, x: float, y: float, w: float | None = None) -> float:
        """Position the text and return its rendered height."""
        w = float(w or 0)
        if self._same(x, y, w, self.text):
            return self._h
        self._w = w
        self.page.coords(self.id, x, y)
        if self.wrap and w:
            self.page.itemconfigure(self.id, width=w, text=self.text)
            box = self.page.bbox(self.id)
            self._h = float(box[3] - box[1]) if box else float(self.fnt.metrics("linespace"))
        else:
            self._apply_text()
            self._h = float(self.fnt.metrics("linespace"))
        return self._h


class Chip(El):
    def __init__(self, page: Page, text: str = "", *, fg: str = MUTED, bg: str = SURFACE_2, size: int = 10) -> None:
        super().__init__(page)
        self.text = text
        self.fnt = font(size, True)
        self.poly = self._poly(fill=bg, outline="")
        self.id = page.create_text(0, 0, text=text, fill=fg, font=self.fnt, anchor="center", tags=(self.tag,))
        self.h = px(22)

    def configure(self, cnf: Any = None, **kw: Any) -> None:
        if "text" in kw and str(kw["text"]) != self.text:
            self.text = str(kw["text"])
            self.page.itemconfigure(self.id, text=self.text)
            self.relayout()
        bg = kw.get("fg_color", kw.get("bg"))
        fg = kw.get("text_color", kw.get("fg"))
        if bg:
            self.page.itemconfigure(self.poly, fill=bg)
        if fg:
            self.page.itemconfigure(self.id, fill=fg)

    config = configure

    def natural_w(self) -> int:
        return self.fnt.measure(self.text) + px(22)

    def place(self, x: float, y: float, w: float | None = None, h: float | None = None) -> None:
        w = w or self.natural_w()
        h = h or self.h
        if self._same(x, y, w, h, self.text):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, h / 2))
        self.page.coords(self.id, x + w / 2, y + h / 2)


class Btn(El):
    STYLES = {
        # fill, hover, text, outline
        "primary": (ACCENT, ACCENT_HOVER, "#FFFFFF", ACCENT),
        "cyan": (CYAN, "#1BB8E0", "#031018", CYAN),
        "secondary": (SURFACE_2, SURFACE_3, TEXT, BORDER),
        "ghost": (SURFACE, SURFACE_2, MUTED, BORDER_SOFT),
        "danger": (DANGER_SOFT, "#4A1D2B", DANGER, "#5B2433"),
        "nav": (SIDEBAR, SURFACE_2, MUTED, SIDEBAR),
        "nav_active": (ACCENT_SOFT, ACCENT_SOFT, TEXT, ACCENT_SOFT),
    }

    def __init__(self, page: Page, text: str = "", command: Callable[[], Any] | None = None, *,
                 style: str = "secondary", size: int = 12, bold: bool = True, icon: str | None = None,
                 state: str = "normal", anchor: str = "center", padx: int = 16, radius: int = 10) -> None:
        super().__init__(page)
        self.text = text
        self.command = command
        self.style = style
        self.state = state
        self.anchor = anchor
        self.padx = px(padx)
        self.radius = px(radius)
        self.fnt = font(size, bold)
        self.over = False
        self._fill_override: str | None = None
        self._fg_override: str | None = None
        self.poly = self._poly(width=1)
        self.id = page.create_text(0, 0, text=text, font=self.fnt, anchor="center" if anchor == "center" else "w",
                                   tags=(self.tag,))
        self.ico = None
        if icon:
            self.ico = page.create_text(0, 0, text=icon, font=font(size + 3, family=ICON_FAMILY), anchor="w",
                                        tags=(self.tag,))
        page.tag_bind(self.tag, "<Enter>", self._enter)
        page.tag_bind(self.tag, "<Leave>", self._leave)
        page.tag_bind(self.tag, "<ButtonRelease-1>", self._click)
        self._apply()

    def _colors(self) -> tuple[str, str, str]:
        fill, hover, fg, outline = self.STYLES[self.style]
        if self._fill_override:
            fill = hover = self._fill_override
        if self._fg_override:
            fg = self._fg_override
        if self.state == "disabled":
            if self.style in {"primary", "cyan"}:
                return SURFACE_3, FAINT, SURFACE_3
            return fill, FAINT, outline
        return (hover if self.over else fill), fg, outline

    def _apply(self) -> None:
        fill, fg, outline = self._colors()
        self.page.itemconfigure(self.poly, fill=fill, outline=outline)
        self.page.itemconfigure(self.id, fill=fg)
        if self.ico:
            self.page.itemconfigure(self.ico, fill=fg)

    def _enter(self, _e: Any = None) -> None:
        self.over = True
        self._apply()
        if self.state == "normal":
            self.page.configure(cursor="hand2")

    def _leave(self, _e: Any = None) -> None:
        self.over = False
        self._apply()
        self.page.configure(cursor="")

    def _click(self, _event: Any) -> None:
        if self.state == "normal" and self.command is not None and self.visible:
            self.command()

    def configure(self, cnf: Any = None, **kw: Any) -> None:
        relayout = False
        if "text" in kw and str(kw["text"]) != self.text:
            self.text = str(kw["text"])
            self.page.itemconfigure(self.id, text=self.text)
            relayout = True
        if "command" in kw:
            self.command = kw["command"]
        if "state" in kw:
            self.state = kw["state"]
        if "style" in kw:
            self.style = kw["style"]
        if "fg_color" in kw:
            self._fill_override = kw["fg_color"]
        if "text_color" in kw:
            self._fg_override = kw["text_color"]
        self._apply()
        if relayout:
            self.relayout()

    config = configure

    def natural_w(self, minimum: int = 0) -> int:
        w = self.fnt.measure(self.text) + 2 * self.padx
        if self.ico:
            w += px(30)
        return max(w, px(minimum))

    def set_active(self, active: bool) -> None:
        self.style = "nav_active" if active else "nav"
        self._apply()

    def place(self, x: float, y: float, w: float | None = None, h: float = 36) -> None:
        w = w or self.natural_w()
        if self._same(x, y, w, h, self.text):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, self.radius))
        cy = y + h / 2
        if self.anchor == "center":
            self.page.coords(self.id, x + w / 2, cy)
        else:
            left = x + self.padx
            if self.ico:
                self.page.coords(self.ico, left, cy)
                left += px(30)
            self.page.coords(self.id, left, cy)


class Bar(El):
    def __init__(self, page: Page, *, height: int = 6, color: str = ACCENT, track: str = SURFACE_3) -> None:
        super().__init__(page)
        self.h = px(height)
        self.color = color
        self.value = 0.0
        self.shown = 0.0
        self.indeterminate = False
        self.phase = 0.0
        self._x = self._y = self._bw = 0.0
        self.track = page.create_line(0, 0, 0, 0, width=self.h, capstyle="round", fill=track, tags=(self.tag,))
        self.fill = page.create_line(0, 0, 0, 0, width=self.h, capstyle="round", fill=color,
                                     tags=(self.tag,), state="hidden")

    def place(self, x: float, y: float, w: float) -> None:
        if self._same(x, y, w):
            return
        self._x, self._y, self._bw = x, y, w
        r = self.h / 2
        cy = y + r
        self.page.coords(self.track, x + r, cy, max(x + r, x + w - r), cy)
        self._draw()

    def _draw(self) -> None:
        if not self.visible:
            return
        r = self.h / 2
        left, right = self._x + r, max(self._x + r, self._x + self._bw - r)
        cy = self._y + r
        if self.indeterminate:
            span = right - left
            seg = span * 0.3
            start = left + (span + seg) * self.phase - seg
            a, b = max(left, start), min(right, start + seg)
            if b > a:
                self.page.coords(self.fill, a, cy, b, cy)
                self.page.itemconfigure(self.fill, state="normal")
            else:
                self.page.itemconfigure(self.fill, state="hidden")
            return
        if self.shown <= 0.002:
            self.page.itemconfigure(self.fill, state="hidden")
            return
        self.page.coords(self.fill, left, cy, left + (right - left) * min(1.0, self.shown), cy)
        self.page.itemconfigure(self.fill, state="normal")

    def set(self, value: float, animate: bool = True) -> None:
        value = max(0.0, min(1.0, float(value)))
        self.indeterminate = False
        if not animate or value < self.value - 0.02:
            self.value = self.shown = value
            self._draw()
            return
        self.value = value
        if abs(self.shown - self.value) > 0.001:
            self.page.animate(self)
        else:
            self.shown = value
            self._draw()

    def set_color(self, color: str) -> None:
        if color != self.color:
            self.color = color
            self.page.itemconfigure(self.fill, fill=color)

    def set_indeterminate(self, on: bool) -> None:
        if on != self.indeterminate:
            self.indeterminate = on
            if on:
                self.page.animate(self)
            else:
                self._draw()

    def step(self, visible: bool) -> bool:
        """Advance one animation frame; True while more frames are needed."""
        if not visible:
            self.shown = self.value
            self._draw()
            return self.indeterminate
        if self.indeterminate:
            self.phase = (self.phase + 0.017) % 1.0
            self._draw()
            return True
        diff = self.value - self.shown
        if abs(diff) < 0.0015:
            self.shown = self.value
            self._draw()
            return False
        self.shown += diff * 0.18 + (0.0007 if diff > 0 else -0.0007)
        self._draw()
        return True


class Field(El):
    """Single-line input: rounded canvas background with a native Entry on top."""

    def __init__(self, page: Page, *, var: tk.StringVar | None = None, placeholder: str = "",
                 readonly: bool = False, size: int = 12, fg: str = TEXT) -> None:
        super().__init__(page)
        self.var = var or tk.StringVar()
        self._ev = tk.StringVar(value=self.var.get())
        self.placeholder = placeholder
        self.fg = fg
        self._ph_on = False
        self._lock = False
        self._focused = False
        self.poly = self._poly(fill=SURFACE_2, outline=BORDER, width=1)
        self.entry = tk.Entry(
            page, textvariable=self._ev, bg=SURFACE_2, fg=fg, insertbackground=TEXT, relief="flat", bd=0,
            highlightthickness=0, font=font(size), readonlybackground=SURFACE_2,
            disabledbackground=SURFACE_2, disabledforeground=FAINT,
            selectbackground=ACCENT, selectforeground="#FFFFFF",
        )
        self.win = page.create_window(0, 0, window=self.entry, anchor="nw", tags=(self.tag,))
        self._ev.trace_add("write", self._on_entry_write)
        self.var.trace_add("write", self._on_var_write)
        self.entry.bind("<FocusIn>", lambda _e: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._focus(False), add="+")
        self.entry.bind("<Control-a>", lambda _e: (self.entry.selection_range(0, "end"), "break")[1])
        if readonly:
            self.entry.configure(state="readonly")
        self._refresh_placeholder()

    def _focus(self, on: bool) -> None:
        self._focused = on
        self.page.itemconfigure(self.poly, outline=ACCENT if on else BORDER)
        self._refresh_placeholder()

    def _on_entry_write(self, *_: Any) -> None:
        if self._ph_on or self._lock:
            return
        self._lock = True
        try:
            self.var.set(self._ev.get())
        finally:
            self._lock = False

    def _on_var_write(self, *_: Any) -> None:
        if self._lock:
            return
        self._lock = True
        try:
            self._ph_on = False
            self.entry.configure(fg=self.fg)
            self._ev.set(self.var.get())
        finally:
            self._lock = False
        self._refresh_placeholder()

    def _refresh_placeholder(self) -> None:
        if not self.placeholder:
            return
        empty = not self.var.get()
        if empty and not self._focused and not self._ph_on:
            self._ph_on = True
            self._lock = True
            self._ev.set(self.placeholder)
            self.entry.configure(fg=FAINT)
            self._lock = False
        elif self._ph_on and (not empty or self._focused):
            self._ph_on = False
            self._lock = True
            self._ev.set(self.var.get())
            self.entry.configure(fg=self.fg)
            self._lock = False

    def get(self) -> str:
        return self.var.get()

    def focus_set(self) -> None:
        self.entry.focus_set()

    def bind(self, sequence: str, func: Any, add: Any = None) -> Any:
        return self.entry.bind(sequence, func, add)

    def configure(self, cnf: Any = None, **kw: Any) -> None:
        if "state" in kw:
            self.entry.configure(state=kw["state"])

    config = configure

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, px(10)))
        pad = px(12)
        self.page.coords(self.win, x + pad, y + px(4))
        self.page.itemconfigure(self.win, width=max(10, int(w - 2 * pad)), height=max(10, int(h - px(8))))


class TextArea(El):
    def __init__(self, page: Page, *, size: int = 12) -> None:
        super().__init__(page)
        self.poly = self._poly(fill=SURFACE_2, outline=BORDER, width=1)
        self.text = tk.Text(
            page, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0,
            font=font(size, family="Consolas"), wrap="none", undo=True,
            selectbackground=ACCENT, selectforeground="#FFFFFF", padx=px(4), pady=px(2),
        )
        self.win = page.create_window(0, 0, window=self.text, anchor="nw", tags=(self.tag,))
        self.text.bind("<FocusIn>", lambda _e: page.itemconfigure(self.poly, outline=ACCENT), add="+")
        self.text.bind("<FocusOut>", lambda _e: page.itemconfigure(self.poly, outline=BORDER), add="+")

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, px(10)))
        pad = px(10)
        self.page.coords(self.win, x + pad, y + px(8))
        self.page.itemconfigure(self.win, width=max(10, int(w - 2 * pad)), height=max(10, int(h - px(16))))


class Select(El):
    def __init__(self, page: Page, values: list[str], var: tk.StringVar, *,
                 command: Callable[[str], Any] | None = None, size: int = 12) -> None:
        super().__init__(page)
        self.values = list(values)
        self.var = var
        self.command = command
        self.state = "normal"
        self.fnt = font(size)
        self.poly = self._poly(fill=SURFACE_2, outline=BORDER, width=1)
        self.id = page.create_text(0, 0, text=var.get(), fill=TEXT, font=self.fnt, anchor="w", tags=(self.tag,))
        self.arrow = page.create_text(0, 0, text="▾", fill=MUTED, font=font(14), anchor="e", tags=(self.tag,))
        self._box: tuple[float, float, float, float] = (0, 0, 0, 0)
        var.trace_add("write", lambda *_: self._sync())
        page.tag_bind(self.tag, "<Enter>", lambda _e: self._hover(True))
        page.tag_bind(self.tag, "<Leave>", lambda _e: self._hover(False))
        page.tag_bind(self.tag, "<ButtonRelease-1>", self._open)

    def _sync(self) -> None:
        self.page.itemconfigure(self.id, text=ellipsize(self.var.get(), self.fnt, max(10, self._box[2] - px(44))))

    def _hover(self, on: bool) -> None:
        if self.state == "normal":
            self.page.itemconfigure(self.poly, outline=ACCENT if on else BORDER)
            self.page.configure(cursor="hand2" if on else "")

    def _open(self, _e: Any = None) -> None:
        if self.state != "normal":
            return
        x, y, _w, h = self._box
        menu = tk.Menu(self.page, tearoff=0, bg=SURFACE_2, fg=TEXT, activebackground=ACCENT,
                       activeforeground="#FFFFFF", bd=0, relief="flat", font=font(12))
        for value in self.values:
            menu.add_command(label=value, command=lambda v=value: self._choose(v))
        try:
            menu.tk_popup(int(self.page.winfo_rootx() + x),
                          int(self.page.winfo_rooty() + (y - self.page.canvasy(0)) + h + 2))
        finally:
            menu.grab_release()

    def _choose(self, value: str) -> None:
        self.var.set(value)
        if self.command:
            self.command(value)

    def configure(self, cnf: Any = None, **kw: Any) -> None:
        if "values" in kw:
            self.values = list(kw["values"])
        if "state" in kw:
            self.state = kw["state"]
            disabled = self.state != "normal"
            self.page.itemconfigure(self.id, fill=FAINT if disabled else TEXT)
            self.page.itemconfigure(self.arrow, fill=FAINT if disabled else MUTED)
            self.page.itemconfigure(self.poly, outline=BORDER_SOFT if disabled else BORDER)

    config = configure

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self._box = (x, y, w, h)
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, px(10)))
        self.page.coords(self.id, x + px(14), y + h / 2)
        self.page.coords(self.arrow, x + w - px(12), y + h / 2 - px(1))
        self._sync()


class Segmented(El):
    def __init__(self, page: Page, values: list[str], var: tk.StringVar, *,
                 command: Callable[[str], Any] | None = None, size: int = 12) -> None:
        super().__init__(page)
        self.values = list(values)
        self.var = var
        self.command = command
        self.back = self._poly(fill=SURFACE_2, outline=BORDER, width=1)
        self.pills: list[int] = []
        self.texts: list[int] = []
        for index, value in enumerate(self.values):
            tag = f"{self.tag}s{index}"
            pill = page.create_polygon(0, 0, 0, 0, 0, 0, smooth=True, splinesteps=8, tags=(self.tag, tag), fill=ACCENT,
                                       outline="")
            txt = page.create_text(0, 0, text=value, font=font(size, True), anchor="center", tags=(self.tag, tag))
            self.pills.append(pill)
            self.texts.append(txt)
            page.tag_bind(tag, "<ButtonRelease-1>", lambda _e, v=value: self._pick(v))
            page.tag_bind(tag, "<Enter>", lambda _e: page.configure(cursor="hand2"))
            page.tag_bind(tag, "<Leave>", lambda _e: page.configure(cursor=""))
        var.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _pick(self, value: str) -> None:
        self.var.set(value)
        if self.command:
            self.command(value)

    def _sync(self) -> None:
        current = self.var.get()
        for value, pill, txt in zip(self.values, self.pills, self.texts):
            active = value == current
            self.page.itemconfigure(pill, fill=ACCENT if active else SURFACE_2)
            self.page.itemconfigure(txt, fill="#FFFFFF" if active else MUTED)

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self.page.coords(self.back, *rrect(x, y, x + w, y + h, px(10)))
        gap = px(4)
        count = len(self.values)
        seg = (w - gap * (count + 1)) / count
        for i, (pill, txt) in enumerate(zip(self.pills, self.texts)):
            x0 = x + gap + i * (seg + gap)
            self.page.coords(pill, *rrect(x0, y + gap, x0 + seg, y + h - gap, px(8)))
            self.page.coords(txt, x0 + seg / 2, y + h / 2)


class Check(El):
    def __init__(self, page: Page, text: str, var: tk.BooleanVar, *, size: int = 12) -> None:
        super().__init__(page)
        self.var = var
        self.fnt = font(size)
        self.box = self._poly(fill=SURFACE_2, outline=BORDER, width=1)
        self.mark = page.create_text(0, 0, text="✓", fill="#FFFFFF", font=font(12, True), anchor="center",
                                     tags=(self.tag,), state="hidden")
        self.id = page.create_text(0, 0, text=text, fill=TEXT, font=self.fnt, anchor="w", tags=(self.tag,))
        page.tag_bind(self.tag, "<ButtonRelease-1>", lambda _e: self.var.set(not self.var.get()))
        page.tag_bind(self.tag, "<Enter>", lambda _e: page.configure(cursor="hand2"))
        page.tag_bind(self.tag, "<Leave>", lambda _e: page.configure(cursor=""))
        var.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        on = bool(self.var.get())
        self.page.itemconfigure(self.box, fill=ACCENT if on else SURFACE_2, outline=ACCENT if on else BORDER)
        self.page.itemconfigure(self.mark, state="normal" if on and self.visible else "hidden")

    def place(self, x: float, y: float, h: float = 24) -> None:
        if self._same(x, y, h):
            return
        s = px(20)
        top = y + (h - s) / 2
        self.page.coords(self.box, *rrect(x, top, x + s, top + s, px(6)))
        self.page.coords(self.mark, x + s / 2, top + s / 2)
        self.page.coords(self.id, x + s + px(10), y + h / 2)
        self._sync()


class Thumb(El):
    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self.poly = self._poly(fill=SURFACE_2, outline=BORDER_SOFT, width=1)
        self.hint = page.create_text(0, 0, text="PREVIEW", fill=FAINT, font=font(11, True), anchor="center",
                                     tags=(self.tag,))
        self.img = page.create_image(0, 0, anchor="center", tags=(self.tag,), state="hidden")
        self.photo: Any = None

    def set_image(self, photo: Any) -> None:
        self.photo = photo
        if photo is None:
            self.page.itemconfigure(self.img, state="hidden", image="")
            self.page.itemconfigure(self.hint, state="normal")
        else:
            self.page.itemconfigure(self.img, image=photo, state="normal")
            self.page.itemconfigure(self.hint, state="hidden")

    def place(self, x: float, y: float, w: float, h: float) -> None:
        if self._same(x, y, w, h):
            return
        self.page.coords(self.poly, *rrect(x, y, x + w, y + h, px(12)))
        self.page.coords(self.hint, x + w / 2, y + h / 2)
        self.page.coords(self.img, x + w / 2, y + h / 2)
