"""Screen Recorder window (Bandicam-style): full screen or a selected area, with quality
settings, system sound and microphone, webcam overlay, cursor and click highlight, a
countdown, an auto-stop timer and global hotkeys. The capture itself is done by
``screen_recorder_engine``.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import crop_geometry as cg
import native
import screen_recorder_engine as engine
from app_logging import get_logger
from theme import C
from ui_kit import px

LOGGER = get_logger("recorder")
APP_TITLE = "Screen Recorder"
IS_WINDOWS = os.name == "nt"

HOTKEYS = {"F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}
FIXED_SIZES = ["1920×1080", "1280×720", "854×480", "640×360", "1080×1920 (vertical)", "720×1280 (vertical)"]
COUNTDOWNS = ["Off", "3 s", "5 s", "10 s"]
CAPTURE_ENGINES = {"Auto": "auto", "GDI (compatible)": "gdigrab", "Desktop Duplication (fast, games)": "ddagrab"}
RING_COLORS = {"left": "#FFC400", "right": "#FF3B30"}
BORDER_COLOR = "#E81123"
KEY_COLOR = "#FF00FE"          # painted inside the chosen area; Windows shows it as see-through
BAR_BG = "#1C1C1E"
BAR_BUTTON = "#3A3A3C"
REC_RED = "#D92D20"
UI_FONT = ("Segoe UI", 9)
UI_FONT_BOLD = ("Segoe UI", 9, "bold")
# Aspect ratios the area can be locked to (width / height).
RATIOS: dict[str, float | None] = {"Free": None, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4,
                                   "1:1": 1.0, "21:9": 21 / 9, "4:5": 4 / 5}
DEFAULTS: dict[str, Any] = {
    "mode": "fullscreen",            # fullscreen, region, fixed
    "monitor": 0,
    "region": None,                  # [x, y, w, h]
    "fixed_size": FIXED_SIZES[1],
    "ratio": "Free",
    "fps": 30,
    "quality": "High",
    "resolution": "Original",
    "encoder": "Auto",
    "container": "MP4",
    "capture_engine": "Auto",
    "system_audio": True,
    "system_volume": 100,
    "mic_enabled": False,
    "microphone": "",
    "mic_volume": 100,
    "webcam_enabled": False,
    "webcam": "",
    "webcam_position": "Bottom right",
    "webcam_size": "Small",
    "draw_mouse": True,
    "highlight_clicks": True,
    "countdown": "3 s",
    "auto_stop": False,
    "auto_stop_minutes": 10,
    "open_editor": True,
    "hotkey": "F12",
    "output_dir": "",
}


# --------------------------------------------------------------------------- Win32 helpers
def _wrapper_hwnd(window: tk.Misc) -> int:
    try:
        return int(window.wm_frame(), 16)
    except Exception:
        try:
            return int(window.winfo_id())
        except Exception:
            return 0


def exclude_from_capture(window: tk.Misc) -> bool:
    """Keep a window (control bar, region border, countdown) out of the recording.

    WDA_EXCLUDEFROMCAPTURE works on Windows 10 2004 and later for both GDI and Desktop
    Duplication capture; older systems simply ignore the request.
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(_wrapper_hwnd(window), 0x11))
    except Exception:
        return False


def make_click_through(window: tk.Misc) -> None:
    """Mouse clicks go through the window to whatever is below it, and it never takes focus."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _wrapper_hwnd(window)
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
    except Exception:
        pass


def foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def restore_foreground(hwnd: int) -> None:
    """Give the keyboard back to the app the user was working in (our overlays took it)."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        import ctypes

        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def show_without_focus(window: tk.Misc, x: int, y: int, width: int, height: int) -> bool:
    """Move a mapped window and bring it to the top without activating it."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        HWND_TOPMOST = -1
        SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
        return bool(ctypes.windll.user32.SetWindowPos(_wrapper_hwnd(window), HWND_TOPMOST, x, y, width, height,
                                                      SWP_NOACTIVATE | SWP_SHOWWINDOW))
    except Exception:
        return False


def _mouse_state() -> tuple[bool, bool, int, int]:
    """Left down, right down, cursor x, cursor y (physical pixels)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    # GetAsyncKeyState follows the physical buttons (swapped buttons are handled by Windows).
    left = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
    right = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
    return left, right, point.x, point.y


def _open_folder(path: Path) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


# --------------------------------------------------------------------------- small widgets
def _shade(color: str, factor: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    if factor >= 1:
        r, g, b = (int(v + (255 - v) * (factor - 1)) for v in (r, g, b))
    else:
        r, g, b = (int(v * factor) for v in (r, g, b))
    return f"#{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"


class FlatButton(tk.Label):
    """A flat, coloured button that looks the same on every Windows version."""

    def __init__(self, master: tk.Misc, text: str, command: Callable[[], Any] | None, *, bg: str = BAR_BUTTON,
                 fg: str = "white", font: tuple[Any, ...] | None = None, padx: int = 10, pady: int = 4) -> None:
        super().__init__(master, text=text, bg=bg, fg=fg, font=font or UI_FONT_BOLD, padx=px(padx), pady=px(pady),
                         cursor="hand2", bd=0)
        self.command = command
        self.base_bg = bg
        self.enabled = True
        self.bind("<Enter>", lambda _e: self._paint(_shade(self.base_bg, 1.22)))
        self.bind("<Leave>", lambda _e: self._paint(self.base_bg))
        self.bind("<ButtonPress-1>", lambda _e: self._paint(_shade(self.base_bg, 0.8)))
        self.bind("<ButtonRelease-1>", self._release)

    def _paint(self, color: str) -> None:
        if self.enabled:
            self.configure(bg=color)

    def _release(self, event: Any) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._paint(_shade(self.base_bg, 1.22) if inside else self.base_bg)
        if inside and self.enabled and self.command is not None:
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(fg="white" if enabled else "#8A8A8A", bg=self.base_bg if enabled else _shade(self.base_bg, 0.7),
                       cursor="hand2" if enabled else "arrow")

    def set_color(self, bg: str) -> None:
        self.base_bg = bg
        self.configure(bg=bg)


class LevelMeter(tk.Canvas):
    """A segmented sound level meter (green, yellow, red) with a falling peak marker."""

    SEGMENTS = 16

    def __init__(self, master: tk.Misc, *, width: int = 90, height: int = 10, bg: str = BAR_BG) -> None:
        super().__init__(master, width=px(width), height=px(height), bg=bg, highlightthickness=0, bd=0)
        self.value = 0.0
        self.peak = 0.0
        self._peak_time = 0.0
        self._drawn: tuple[int, int] | None = None
        self.bind("<Configure>", lambda _e: self._draw(force=True))

    def set(self, level: float) -> None:
        """``level`` is the raw peak (0..1); it is shown on a dB scale."""
        fill = engine.level_fraction(level)
        # Fast attack, slower release, so speech looks like a real meter and not flicker.
        self.value = fill if fill > self.value else max(fill, self.value - 0.06)
        now = time.monotonic()
        if fill >= self.peak or now - self._peak_time > 1.2:
            self.peak, self._peak_time = fill, now
        self._draw()

    def _draw(self, force: bool = False) -> None:
        count = self.SEGMENTS
        lit = int(round(self.value * count))
        peak = min(count - 1, int(self.peak * count)) if self.peak > 0.02 else -1
        if not force and self._drawn == (lit, peak):
            return
        self._drawn = (lit, peak)
        self.delete("all")
        width, height = max(10, self.winfo_width()), max(4, self.winfo_height())
        gap = 1
        seg_w = (width - gap * (count - 1)) / count
        for index in range(count):
            x = index * (seg_w + gap)
            zone = "#2BD46A" if index < count * 0.65 else "#FFC400" if index < count * 0.87 else "#FF3B30"
            on = index < lit or index == peak
            self.create_rectangle(x, 0, x + seg_w, height, outline="", fill=zone if on else "#3A3A3A")


# --------------------------------------------------------------------------- overlays
class RegionSelector(tk.Toplevel):
    """Area picker: the desktop is dimmed, the chosen box stays clear.

    Drag to draw a box (a ratio can be locked), drag its edges or corners to resize, drag
    the middle grip or the toolbar's grip to move it, or type an exact width × height. As
    soon as a box exists a toolbar sits on its top corner with Record, OK and Cancel, so a
    recording starts with one click. A click without dragging picks the screen under it.
    """

    def __init__(self, master: tk.Misc, bounds: tuple[int, int, int, int], monitors: list[engine.Monitor],
                 initial: tuple[int, int, int, int] | None,
                 on_done: Callable[[tuple[int, int, int, int] | None, str], None],
                 *, fixed_size: tuple[int, int] | None = None, ratio: str = "Free",
                 on_ratio_change: Callable[[str], None] | None = None) -> None:
        super().__init__(master)
        self.bounds = bounds
        self.monitors = monitors
        self.on_done = on_done
        self.fixed_size = fixed_size
        self.ratio_name = ratio if ratio in RATIOS else "Free"
        self.on_ratio_change = on_ratio_change
        self._done = False
        bx, by, bw, bh = bounds
        self.overrideredirect(True)
        self.geometry(f"{bw}x{bh}+{bx}+{by}")
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.6)
        except tk.TclError:
            pass
        # The inside of the box is painted in a key colour that Windows makes fully
        # transparent, so what will be recorded is seen clearly (and stays usable).
        self.clear_inside = False
        if IS_WINDOWS:
            try:
                self.attributes("-transparentcolor", KEY_COLOR)
                self.clear_inside = True
            except tk.TclError:
                pass
        self.configure(bg="black", cursor="crosshair")
        self.canvas = tk.Canvas(self, bg="#0A0A0A", highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.rect: cg.Rect | None = None
        if fixed_size:
            w, h = fixed_size
            if initial and (initial[2], initial[3]) == (engine.even(w), engine.even(h)):
                self.rect = (initial[0] - bx, initial[1] - by, w, h)
            else:
                self.rect = cg.keep_inside(((bw - w) / 2, (bh - h) / 2, w, h), bw, bh)
        elif initial:
            x, y, w, h = initial
            self.rect = cg.keep_inside((x - bx, y - by, w, h), bw, bh)
        self.drag: dict[str, Any] | None = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._finish("record") if self.rect else None)
        self.bind("<Return>", lambda _e: self._finish("record"))
        self.bind("<KP_Enter>", lambda _e: self._finish("record"))
        self.bind("<Escape>", lambda _e: self._finish("cancel"))
        self.canvas.bind("<Button-3>", lambda _e: self._finish("cancel"))
        for key, (dx, dy) in {"<Left>": (-1, 0), "<Right>": (1, 0), "<Up>": (0, -1), "<Down>": (0, 1)}.items():
            self.bind(key, lambda _e, dx=dx, dy=dy: self._nudge(dx, dy))
            self.bind(f"<Shift-{key[1:]}", lambda _e, dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))
        self.toolbar = SelectionToolbar(self)
        self.update_idletasks()
        self.after(30, self._take_focus)
        self._draw()

    # ------------------------------------------------------------------ helpers
    def _take_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass
        self.toolbar.lift_above()

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.bounds[2], self.bounds[3]

    @property
    def ratio(self) -> float | None:
        return None if self.fixed_size else RATIOS.get(self.ratio_name)

    def _grip_hit(self, x: float, y: float) -> bool:
        if not self.rect:
            return False
        rx, ry, rw, rh = self.rect
        if rw < 90 or rh < 90:
            return False
        cx, cy = rx + rw / 2, ry + rh / 2
        return abs(x - cx) <= px(22) and abs(y - cy) <= px(22)

    def _handle_at(self, x: float, y: float) -> str | None:
        if not self.rect:
            return None
        if self._grip_hit(x, y):
            return "move"
        handle = cg.hit_test(self.rect, x, y, px(10))
        if self.fixed_size and handle:
            handle = "move"
        return handle

    def set_rect(self, rect: cg.Rect) -> None:
        self.rect = cg.keep_inside(rect, *self.frame_size)
        self._draw()

    def set_ratio(self, name: str) -> None:
        self.ratio_name = name if name in RATIOS else "Free"
        if self.on_ratio_change is not None:
            self.on_ratio_change(self.ratio_name)
        if self.rect and self.ratio:
            self.rect = cg.fit_ratio(self.rect, self.ratio, *self.frame_size)
        self._draw()

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        if not self.rect:
            return None
        bx, by, _bw, _bh = self.bounds
        x, y, w, h = self.rect
        return engine.normalize_region((x + bx, y + by, w, h), self.bounds)

    # ------------------------------------------------------------------ mouse
    def _press(self, event: Any) -> None:
        fw, fh = self.frame_size
        handle = self._handle_at(event.x, event.y)
        if handle:
            self.drag = {"mode": handle, "x": event.x, "y": event.y, "rect": self.rect, "moved": False}
        elif self.fixed_size:
            w, h = self.fixed_size
            self.rect = cg.keep_inside((event.x - w / 2, event.y - h / 2, w, h), fw, fh)
            self.drag = {"mode": "move", "x": event.x, "y": event.y, "rect": self.rect, "moved": True}
        else:
            self.drag = {"mode": "new", "x": event.x, "y": event.y, "rect": self.rect, "moved": False}
        self.toolbar.hide()
        self._draw()

    def _motion(self, event: Any) -> None:
        if not self.drag:
            return
        fw, fh = self.frame_size
        dx, dy = event.x - self.drag["x"], event.y - self.drag["y"]
        if abs(dx) + abs(dy) > 3:
            self.drag["moved"] = True
        if self.drag["mode"] == "new":
            if self.drag["moved"]:
                self.rect = cg.from_points(self.drag["x"], self.drag["y"], event.x, event.y, fw, fh, self.ratio)
        else:
            self.rect = cg.resize(self.drag["rect"], self.drag["mode"], dx, dy, fw, fh, ratio=self.ratio,
                                  min_size=engine.MIN_REGION)
        self._draw(show_toolbar=False)

    def _release(self, event: Any) -> None:
        drag, self.drag = self.drag, None
        if drag and drag["mode"] == "new" and not drag["moved"]:
            # A plain click: the whole screen under the mouse.
            bx, by, _bw, _bh = self.bounds
            for monitor in self.monitors:
                if monitor.left <= event.x + bx < monitor.left + monitor.width and \
                        monitor.top <= event.y + by < monitor.top + monitor.height:
                    self.rect = (monitor.left - bx, monitor.top - by, monitor.width, monitor.height)
                    break
            else:
                self.rect = (0, 0, *self.frame_size)
            if self.ratio:
                self.rect = cg.fit_ratio(self.rect, self.ratio, *self.frame_size)
        if self.rect and (self.rect[2] < engine.MIN_REGION or self.rect[3] < engine.MIN_REGION):
            self.rect = drag["rect"] if drag else None
        self._draw()

    def _hover(self, event: Any) -> None:
        handle = self._handle_at(event.x, event.y)
        cursors = {"move": "fleur", "n": "top_side", "s": "bottom_side", "e": "right_side", "w": "left_side",
                   "nw": "top_left_corner", "ne": "top_right_corner", "sw": "bottom_left_corner",
                   "se": "bottom_right_corner"}
        self.canvas.configure(cursor=cursors.get(handle or "", "crosshair"))

    def _nudge(self, dx: int, dy: int) -> None:
        if self.rect:
            self.rect = cg.move(self.rect, dx, dy, *self.frame_size)
            self._draw()

    # ------------------------------------------------------------------ drawing
    def _draw(self, show_toolbar: bool = True) -> None:
        canvas = self.canvas
        canvas.delete("all")
        fw, fh = self.frame_size
        if self.fixed_size:
            text = "Drag the box to the part of the screen to record, then press ● REC (or Enter). Esc = cancel"
        elif self.rect:
            text = ("Drag the edges or corners to resize • drag ✥ or the toolbar grip to move • "
                    "type an exact size in the toolbar • ● REC starts • Esc = cancel")
        else:
            text = "Drag to select the area to record  •  click a screen to take all of it  •  Esc = cancel"
        if self.rect:
            x, y, w, h = self.rect
            inside = KEY_COLOR if self.clear_inside else ""
            canvas.create_rectangle(x, y, x + w, y + h, outline=BORDER_COLOR, width=2, fill=inside)
            # Rule-of-thirds guides help line things up (thin, only on a big enough box).
            if w > 240 and h > 160:
                for i in (1, 2):
                    canvas.create_line(x + w * i / 3, y, x + w * i / 3, y + 6, fill=BORDER_COLOR)
                    canvas.create_line(x, y + h * i / 3, x + 6, y + h * i / 3, fill=BORDER_COLOR)
            if not self.fixed_size:
                size = px(5)
                for hx, hy in ((x, y), (x + w / 2, y), (x + w, y), (x + w, y + h / 2), (x + w, y + h),
                               (x + w / 2, y + h), (x, y + h), (x, y + h / 2)):
                    canvas.create_rectangle(hx - size, hy - size, hx + size, hy + size, fill=BORDER_COLOR,
                                            outline="white")
            if w >= 90 and h >= 90:
                cx, cy, r = x + w / 2, y + h / 2, px(18)
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#202020", outline="white", width=1)
                canvas.create_text(cx, cy, text="✥", fill="white", font=("Segoe UI Symbol", 14))
            _ex, _ey, ew, eh = engine.normalize_region((x, y, w, h))
            label = f"{ew} × {eh}"
            ly = y - px(24) if y > px(30) else y + h + px(4)
            canvas.create_rectangle(x, ly, x + px(14) + px(8) * len(label), ly + px(20), fill="#111111",
                                    outline=BORDER_COLOR)
            canvas.create_text(x + px(7), ly + px(10), text=label, fill="white", anchor="w", font=UI_FONT_BOLD)
        hint_y = px(34)
        if self.rect and self.rect[1] < px(70):
            hint_y = fh - px(34)
        canvas.create_rectangle(fw / 2 - px(430), hint_y - px(16), fw / 2 + px(430), hint_y + px(16), fill="#111111",
                                outline="")
        canvas.create_text(fw / 2, hint_y, text=text, fill="white", font=UI_FONT, justify="center")
        if self.rect and show_toolbar and not self.drag:
            self.toolbar.show_for(self.rect)
        elif not self.rect:
            self.toolbar.hide()

    # ------------------------------------------------------------------ result
    def _finish(self, action: str) -> None:
        if self._done:
            return
        if action != "cancel" and not self.rect:
            return
        self._done = True
        result = self.screen_rect() if action != "cancel" else None
        try:
            self.toolbar.destroy()
        except tk.TclError:
            pass
        self.destroy()
        self.on_done(result, action)


class SelectionToolbar(tk.Toplevel):
    """Record / OK / Cancel, the exact size and the ratio, on the corner of the chosen box."""

    def __init__(self, selector: RegionSelector) -> None:
        super().__init__(selector)
        self.selector = selector
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BAR_BG, highlightthickness=1, highlightbackground="#4A4A4A")
        pad = {"padx": px(3), "pady": px(4)}
        grip = tk.Label(self, text="⠿", fg="#9A9A9A", bg=BAR_BG, cursor="fleur", font=UI_FONT_BOLD)
        grip.pack(side="left", padx=(px(6), px(2)))
        grip.bind("<ButtonPress-1>", self._grip_press)
        grip.bind("<B1-Motion>", self._grip_move)
        self.w_var = tk.StringVar(self)
        self.h_var = tk.StringVar(self)
        fixed = selector.fixed_size is not None
        self.entries: dict[str, tk.Entry] = {}
        for var, name in ((self.w_var, "W"), (self.h_var, "H")):
            tk.Label(self, text=name, fg="#9A9A9A", bg=BAR_BG, font=UI_FONT).pack(side="left", padx=(px(4), 0))
            entry = tk.Entry(self, textvariable=var, width=5, bg="#2B2B2B", fg="white", insertbackground="white",
                             relief="flat", font=UI_FONT_BOLD, justify="center",
                             disabledbackground="#2B2B2B", disabledforeground="#9A9A9A")
            entry.pack(side="left", ipady=px(2), **pad)
            self.entries[name] = entry
            entry.bind("<Return>", self._apply_size)
            entry.bind("<KP_Enter>", self._apply_size)
            entry.bind("<FocusOut>", self._apply_size)
            entry.bind("<Button-1>", lambda e: e.widget.focus_force())
            if fixed:
                entry.configure(state="disabled")
        self.ratio_button = FlatButton(self, "", self._ratio_menu, bg=BAR_BUTTON, font=UI_FONT)
        self.ratio_button.pack(side="left", **pad)
        if fixed:
            self.ratio_button.set_enabled(False)
        self.full_button = FlatButton(self, "⛶", self._whole_screen, bg=BAR_BUTTON, font=UI_FONT_BOLD, padx=7)
        self.full_button.pack(side="left", **pad)
        tk.Frame(self, width=1, bg="#4A4A4A").pack(side="left", fill="y", pady=px(5), padx=px(3))
        FlatButton(self, "● REC", lambda: selector._finish("record"), bg=REC_RED).pack(side="left", **pad)
        FlatButton(self, "✓ OK", lambda: selector._finish("ok"), bg=BAR_BUTTON).pack(side="left", **pad)
        FlatButton(self, "✕", lambda: selector._finish("cancel"), bg=BAR_BUTTON, padx=8).pack(
            side="left", padx=(px(3), px(6)), pady=px(4))
        self._menu = tk.Menu(self, tearoff=0)
        for name in RATIOS:
            self._menu.add_command(label=name if name != "Free" else "Free (any size)",
                                   command=lambda name=name: self._set_ratio(name))
        self._grip_origin: tuple[int, int, cg.Rect] | None = None
        self.update_idletasks()
        exclude_from_capture(self)

    def lift_above(self) -> None:
        try:
            if self.winfo_viewable():
                self.lift()
                self.attributes("-topmost", True)
        except tk.TclError:
            pass

    def hide(self) -> None:
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def show_for(self, rect: cg.Rect) -> None:
        x, y, w, h = rect
        _ex, _ey, ew, eh = engine.normalize_region((x, y, w, h))
        focused = self.focus_get()
        if not isinstance(focused, tk.Entry):
            self.w_var.set(str(ew))
            self.h_var.set(str(eh))
        self.ratio_button.configure(text=f"Ratio {self.selector.ratio_name} ▾")
        self.update_idletasks()
        tw, th = self.winfo_reqwidth(), self.winfo_reqheight()
        bx, by, bw, bh = self.selector.bounds
        gap = px(6)
        left = max(0, min(int(x + w - tw), bw - tw))
        if y - th - gap >= 0:
            top = y - th - gap
        elif y + h + gap + th <= bh:
            top = y + h + gap
        else:
            top = y + gap
        self.geometry(f"+{int(bx + left)}+{int(by + top)}")
        if not self.winfo_viewable():
            self.deiconify()
        self.lift_above()

    def _apply_size(self, _event: Any = None) -> str | None:
        selector = self.selector
        if selector.fixed_size or not selector.rect:
            return None
        try:
            w = int(float(self.w_var.get()))
            h = int(float(self.h_var.get()))
        except ValueError:
            selector._draw()
            return "break"
        fw, fh = selector.frame_size
        ratio = selector.ratio
        if ratio:
            # The field just typed in decides; the other one follows the ratio.
            if _event is not None and getattr(_event, "widget", None) is self.entries.get("H"):
                w = round(h * ratio)
            else:
                h = round(w / ratio)
        w = max(engine.MIN_REGION, min(w, fw))
        h = max(engine.MIN_REGION, min(h, fh))
        x, y, _w, _h = selector.rect
        selector.set_rect((x, y, w, h))
        if _event is not None and getattr(_event, "keysym", "") in {"Return", "KP_Enter"}:
            selector.focus_force()
        return "break"

    def _ratio_menu(self) -> None:
        try:
            self._menu.tk_popup(self.ratio_button.winfo_rootx(),
                                self.ratio_button.winfo_rooty() + self.ratio_button.winfo_height())
        finally:
            self._menu.grab_release()

    def _set_ratio(self, name: str) -> None:
        self.selector.set_ratio(name)

    def _whole_screen(self) -> None:
        selector = self.selector
        rect = selector.rect or (0, 0, *selector.frame_size)
        bx, by, _bw, _bh = selector.bounds
        cx, cy = rect[0] + rect[2] / 2 + bx, rect[1] + rect[3] / 2 + by
        target: cg.Rect = (0, 0, *selector.frame_size)
        for monitor in selector.monitors:
            if monitor.left <= cx < monitor.left + monitor.width and monitor.top <= cy < monitor.top + monitor.height:
                target = (monitor.left - bx, monitor.top - by, monitor.width, monitor.height)
                break
        if selector.ratio:
            target = cg.fit_ratio(target, selector.ratio, *selector.frame_size)
        selector.set_rect(target)

    def _grip_press(self, event: Any) -> None:
        if self.selector.rect:
            self._grip_origin = (event.x_root, event.y_root, self.selector.rect)

    def _grip_move(self, event: Any) -> None:
        if not self._grip_origin:
            return
        x0, y0, rect = self._grip_origin
        self.selector.set_rect(cg.move(rect, event.x_root - x0, event.y_root - y0, *self.selector.frame_size))


class RegionBorder:
    """A thin red frame drawn just outside the recorded area (never inside the video)."""

    def __init__(self, master: tk.Misc, region: tuple[int, int, int, int]) -> None:
        self.windows: list[tk.Toplevel] = []
        x, y, w, h = region
        t = 3
        for gx, gy, gw, gh in ((x - t, y - t, w + 2 * t, t), (x - t, y + h, w + 2 * t, t),
                               (x - t, y, t, h), (x + w, y, t, h)):
            win = tk.Toplevel(master)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=BORDER_COLOR)
            win.geometry(f"{max(1, gw)}x{max(1, gh)}+{gx}+{gy}")
            win.update_idletasks()
            make_click_through(win)
            exclude_from_capture(win)
            self.windows.append(win)

    def destroy(self) -> None:
        for win in self.windows:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self.windows = []


class ClickHighlighter:
    """Shows a ripple around the cursor when the mouse is clicked (yellow = left, red = right).

    The ripple is a small click-through window, so it is part of the screen and gets
    recorded like everything else; nothing has to be added to the video afterwards.
    """

    SIZE = 110

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        key = "#FF00FE"
        self.window.configure(bg=key)
        try:
            self.window.attributes("-transparentcolor", key)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.window, width=self.SIZE, height=self.SIZE, bg=key, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.window.update_idletasks()
        make_click_through(self.window)
        # On Windows the ring stays mapped, parked off-screen, and is moved with SetWindowPos
        # (no activation): showing a window normally would take the keyboard away from the
        # app being recorded on every click.
        self._parked = (-32000, -32000)
        if IS_WINDOWS:
            previous = foreground_window()
            self.window.geometry(f"{self.SIZE}x{self.SIZE}+{self._parked[0]}+{self._parked[1]}")
            self.window.deiconify()
            self.window.update_idletasks()
            make_click_through(self.window)
            restore_foreground(previous)
        self._state = (False, False)
        self._after: str | None = None
        self._ripple: tuple[float, str] | None = None
        self.active = False

    def start(self) -> None:
        if not IS_WINDOWS or self.active:
            return
        self.active = True
        self._poll()

    def stop(self) -> None:
        self.active = False
        self._ripple = None
        if self._after:
            try:
                self.master.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        self._hide()

    def _hide(self) -> None:
        try:
            if IS_WINDOWS:
                show_without_focus(self.window, *self._parked, self.SIZE, self.SIZE)
            else:
                self.window.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self.stop()
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # Ripple, like Bandicam: shown only after a click, rings spread from the cursor like a water
    # drop and fade out; the whole effect follows the cursor while it lasts.
    DURATION = 0.75   # seconds
    RINGS = 3
    RING_DELAY = 0.13  # seconds between rings

    def _poll(self) -> None:
        if not self.active:
            return
        try:
            left, right, x, y = _mouse_state()
            if left and not self._state[0]:
                self._start_ripple(RING_COLORS["left"])
            elif right and not self._state[1]:
                self._start_ripple(RING_COLORS["right"])
            self._state = (left, right)
            if self._ripple is not None:
                self._draw_ripple(x, y)
        except Exception:
            pass
        self._after = self.master.after(15, self._poll)

    def _start_ripple(self, color: str) -> None:
        self._ripple = (time.monotonic(), color)

    def _draw_ripple(self, x: int, y: int) -> None:
        started, color = self._ripple  # type: ignore[misc]
        elapsed = time.monotonic() - started
        total = self.DURATION + self.RING_DELAY * (self.RINGS - 1)
        if elapsed >= total:
            self._ripple = None
            self._hide()
            return
        size = self.SIZE
        half = size / 2
        self.canvas.delete("all")
        for ring in range(self.RINGS):
            p = (elapsed - ring * self.RING_DELAY) / self.DURATION
            if p <= 0 or p >= 1:
                continue
            ease = 1 - (1 - p) ** 2  # fast at first, slowing like a spreading wave
            radius = 6 + ease * (half - 10)
            width = max(1.0, 5.0 * (1 - p))
            self.canvas.create_oval(half - radius, half - radius, half + radius, half + radius,
                                    outline=color, width=width)
        # One alpha for the whole window: the newest ring is the most visible one.
        alpha = max(0.0, min(0.95, 1.0 - (elapsed / total) ** 2))
        try:
            self.window.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        self.canvas.update_idletasks()
        left, top = int(x - half), int(y - half)
        if not show_without_focus(self.window, left, top, size, size):
            self.window.geometry(f"{size}x{size}+{left}+{top}")
            self.window.deiconify()
            self.window.lift()


class Countdown(tk.Toplevel):
    """Big 3-2-1 in the middle of the area to record (excluded from the recording)."""

    def __init__(self, master: tk.Misc, region: tuple[int, int, int, int], seconds: int,
                 on_done: Callable[[bool], None]) -> None:
        super().__init__(master)
        self.on_done = on_done
        self.remaining = seconds
        size = px(150)
        x, y, w, h = region
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        self.configure(bg="#111111")
        self.geometry(f"{size}x{size}+{x + w // 2 - size // 2}+{y + h // 2 - size // 2}")
        self.label = tk.Label(self, text="", fg="white", bg="#111111", font=("Segoe UI", 64, "bold"))
        self.label.pack(expand=True, fill="both")
        tk.Label(self, text="Esc = cancel", fg="#BBBBBB", bg="#111111", font=("Segoe UI", 9)).pack(pady=(0, 6))
        self.bind("<Escape>", lambda _e: self._finish(False))
        self.update_idletasks()
        exclude_from_capture(self)
        self.focus_force()
        self._tick()

    def _tick(self) -> None:
        if self.remaining <= 0:
            self._finish(True)
            return
        self.label.configure(text=str(self.remaining))
        self.remaining -= 1
        self.after(1000, self._tick)

    def _finish(self, go: bool) -> None:
        if not self.winfo_exists():
            return
        self.destroy()
        self.on_done(go)


class ControlBar(tk.Toplevel):
    """Always-on-top bar on the corner of the recorded area (kept out of the video).

    Shows the time, the file size and live sound meters (microphone and system sound), with
    Pause/Resume and Stop. The grip on the left moves it.
    """

    def __init__(self, master: "ScreenRecorderWindow", region: tuple[int, int, int, int],
                 screen: tuple[int, int, int, int], *, show_mic: bool, show_system: bool) -> None:
        super().__init__(master)
        self.recorder = master
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BAR_BG, highlightthickness=1, highlightbackground="#4A4A4A")
        pad = {"padx": px(3), "pady": px(4)}
        grip = tk.Label(self, text="⠿", fg="#9A9A9A", bg=BAR_BG, cursor="fleur", font=UI_FONT_BOLD)
        grip.pack(side="left", padx=(px(6), px(2)))
        grip.bind("<ButtonPress-1>", self._start_move)
        grip.bind("<B1-Motion>", self._move)
        self.dot = tk.Label(self, text="●", fg=REC_RED, bg=BAR_BG, font=("Segoe UI", 12, "bold"))
        self.dot.pack(side="left", padx=(px(2), 0))
        self.state_label = tk.Label(self, text="REC", fg="white", bg=BAR_BG, font=UI_FONT_BOLD)
        self.state_label.pack(side="left", padx=(px(2), px(4)))
        self.time_label = tk.Label(self, text="00:00:00", fg="white", bg=BAR_BG, font=("Consolas", 13, "bold"))
        self.time_label.pack(side="left", **pad)
        self.size_label = tk.Label(self, text="0 KB", fg="#A8A8A8", bg=BAR_BG, font=UI_FONT, width=8, anchor="w")
        self.size_label.pack(side="left", **pad)
        self.meters: dict[str, LevelMeter] = {}
        for key, icon, shown in (("mic", "🎤", show_mic), ("system", "🔊", show_system)):
            if not shown:
                continue
            box = tk.Frame(self, bg=BAR_BG)
            box.pack(side="left", padx=px(4))
            tk.Label(box, text=icon, fg="white", bg=BAR_BG, font=("Segoe UI Emoji", 10)).pack(side="left")
            meter = LevelMeter(box, width=64, height=9)
            meter.pack(side="left", padx=(px(2), 0))
            self.meters[key] = meter
        tk.Frame(self, width=1, bg="#4A4A4A").pack(side="left", fill="y", pady=px(5), padx=px(3))
        self.pause_button = FlatButton(self, "❚❚ Pause", master.toggle_pause, bg=BAR_BUTTON)
        self.pause_button.pack(side="left", **pad)
        self.stop_button = FlatButton(self, "■ Stop", master.stop_recording, bg=REC_RED)
        self.stop_button.pack(side="left", padx=(px(3), px(6)), pady=px(4))
        self.update_idletasks()
        self._place(region, screen)
        exclude_from_capture(self)
        self._blink = True

    def _place(self, region: tuple[int, int, int, int], screen: tuple[int, int, int, int]) -> None:
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x, y, w, h = region
        sx, sy, sw, sh = screen
        gap = px(6)
        if (x, y, w, h) == (sx, sy, sw, sh):
            # Full screen: top centre (the bar is kept out of the video).
            left, top = sx + (sw - width) // 2, sy + gap
        else:
            # On the top right corner of the area, outside it if there is room.
            left = max(sx, min(x + w - width, sx + sw - width))
            if y - height - gap >= sy:
                top = y - height - gap
            elif y + h + gap + height <= sy + sh:
                top = y + h + gap
            else:
                top = y + gap
        self.geometry(f"+{left}+{top}")

    def _start_move(self, event: Any) -> None:
        self._offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _move(self, event: Any) -> None:
        ox, oy = getattr(self, "_offset", (0, 0))
        self.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def update_status(self, elapsed: float, size: int, paused: bool) -> None:
        self.time_label.configure(text=engine.format_duration(elapsed))
        self.size_label.configure(text=engine.format_size(size))
        self._blink = not self._blink
        if paused:
            self.dot.configure(text="❚❚", fg="#FFC400")
            self.state_label.configure(text="PAUSED", fg="#FFC400")
        else:
            self.dot.configure(text="●", fg=REC_RED if self._blink else "#7A1A14")
            self.state_label.configure(text="REC", fg="white")
        self.pause_button.configure(text="▶ Resume" if paused else "❚❚ Pause")
        self.pause_button.set_color("#2E7D32" if paused else BAR_BUTTON)

    def update_levels(self, levels: dict[str, float]) -> None:
        for key, meter in self.meters.items():
            meter.set(levels.get(key, 0.0))

    def set_finishing(self) -> None:
        self.dot.configure(text="…", fg="#BBBBBB")
        self.state_label.configure(text="SAVING", fg="#BBBBBB")
        self.pause_button.set_enabled(False)
        self.stop_button.configure(text="Saving…")
        self.stop_button.set_enabled(False)
        for meter in self.meters.values():
            meter.set(0.0)


# --------------------------------------------------------------------------- main window
class ScreenRecorderWindow(tk.Toplevel):
    def __init__(self, parent: Any, *, output_dir: Path, settings: dict[str, Any] | None = None,
                 save_settings: Callable[[dict[str, Any]], None] | None = None,
                 open_in_editor: Callable[[Path], None] | None = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.default_output_dir = Path(output_dir)
        self.save_callback = save_settings
        self.open_in_editor = open_in_editor
        self.config_data = {**DEFAULTS, **(settings or {})}
        self.ui_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()
        self.session: engine.RecordingSession | None = None
        self.state = "idle"  # idle, countdown, recording, paused, finishing
        self.monitors = engine.list_monitors()
        self.region: tuple[int, int, int, int] | None = None
        self.border: RegionBorder | None = None
        self.bar: ControlBar | None = None
        self.highlighter: ClickHighlighter | None = None
        self.countdown: Countdown | None = None
        self.hotkeys: list[Any] = []
        self.usable_encoders: list[str] = ["x264"]
        self.webcams: list[str] = []
        self.microphones: list[str] = []
        self._closing = False
        self._tick_id: str | None = None
        self._meter_id: str | None = None
        self.selector: RegionSelector | None = None
        self.monitors_live: dict[str, engine.LevelMonitor] = {}
        self.audio_error = ""

        self.title(APP_TITLE)
        self.configure(bg=native.dialog_bg(self))
        self.resizable(False, False)
        try:
            icon = getattr(parent, "_icon_file", None)
            if icon:
                self.iconbitmap(icon)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroyed, add="+")

        self._make_vars()
        self._build()
        self._sync_mode()
        self.after(40, self._drain_queue)
        threading.Thread(target=self._probe_devices, daemon=True).start()
        self._register_hotkeys()
        self.after(400, self._offer_recovery)

    # ------------------------------------------------------------------ settings
    def _make_vars(self) -> None:
        c = self.config_data
        self.mode_var = tk.StringVar(self, value=c["mode"] if c["mode"] in {"fullscreen", "region", "fixed"}
                                     else "fullscreen")
        labels = self._monitor_labels()
        monitor_index = int(c.get("monitor") or 0)
        self.monitor_var = tk.StringVar(self, value=labels[min(monitor_index, len(labels) - 1)])
        self.fixed_var = tk.StringVar(self, value=c["fixed_size"] if c["fixed_size"] in FIXED_SIZES else FIXED_SIZES[1])
        self.ratio_var = tk.StringVar(self, value=c.get("ratio") if c.get("ratio") in RATIOS else "Free")
        self.fps_var = tk.StringVar(self, value=str(c["fps"] if int(c["fps"]) in engine.FRAME_RATES else 30))
        self.quality_var = tk.StringVar(self, value=c["quality"] if c["quality"] in engine.QUALITIES else "High")
        self.resolution_var = tk.StringVar(self, value=c["resolution"] if c["resolution"] in engine.RESOLUTIONS
                                           else "Original")
        self.encoder_var = tk.StringVar(self, value=c["encoder"] if c["encoder"] in engine.ENCODERS else "Auto")
        self.container_var = tk.StringVar(self, value=c["container"] if c["container"] in engine.CONTAINERS
                                          else "MP4")
        self.engine_var = tk.StringVar(self, value=c["capture_engine"] if c["capture_engine"] in CAPTURE_ENGINES
                                       else "Auto")
        self.system_var = tk.BooleanVar(self, value=bool(c["system_audio"]))
        self.system_volume_var = tk.IntVar(self, value=int(c["system_volume"]))
        self.mic_var = tk.BooleanVar(self, value=bool(c["mic_enabled"]))
        self.mic_device_var = tk.StringVar(self, value=str(c["microphone"] or ""))
        self.mic_volume_var = tk.IntVar(self, value=int(c["mic_volume"]))
        self.webcam_var = tk.BooleanVar(self, value=bool(c["webcam_enabled"]))
        self.webcam_device_var = tk.StringVar(self, value=str(c["webcam"] or ""))
        self.webcam_position_var = tk.StringVar(self, value=c["webcam_position"]
                                                if c["webcam_position"] in engine.WEBCAM_POSITIONS else "Bottom right")
        self.webcam_size_var = tk.StringVar(self, value=c["webcam_size"] if c["webcam_size"] in engine.WEBCAM_SIZES
                                            else "Small")
        self.mouse_var = tk.BooleanVar(self, value=bool(c["draw_mouse"]))
        self.clicks_var = tk.BooleanVar(self, value=bool(c["highlight_clicks"]))
        self.countdown_var = tk.StringVar(self, value=c["countdown"] if c["countdown"] in COUNTDOWNS else "3 s")
        self.auto_stop_var = tk.BooleanVar(self, value=bool(c["auto_stop"]))
        self.auto_stop_minutes_var = tk.StringVar(self, value=str(c["auto_stop_minutes"]))
        self.open_editor_var = tk.BooleanVar(self, value=bool(c["open_editor"]))
        self.hotkey_var = tk.StringVar(self, value=c["hotkey"] if c["hotkey"] in HOTKEYS else "F12")
        self.output_dir = Path(c["output_dir"]) if c.get("output_dir") else self.default_output_dir
        region = c.get("region")
        if isinstance(region, (list, tuple)) and len(region) == 4:
            try:
                self.region = tuple(int(v) for v in region)  # type: ignore[assignment]
            except (TypeError, ValueError):
                self.region = None

    def _collect_settings(self) -> dict[str, Any]:
        labels = self._monitor_labels()
        try:
            minutes = max(1, int(float(self.auto_stop_minutes_var.get())))
        except ValueError:
            minutes = 10
        return {
            "mode": self.mode_var.get(),
            "monitor": labels.index(self.monitor_var.get()) if self.monitor_var.get() in labels else 0,
            "region": list(self.region) if self.region else None,
            "fixed_size": self.fixed_var.get(),
            "ratio": self.ratio_var.get(),
            "fps": int(self.fps_var.get()),
            "quality": self.quality_var.get(),
            "resolution": self.resolution_var.get(),
            "encoder": self.encoder_var.get(),
            "container": self.container_var.get(),
            "capture_engine": self.engine_var.get(),
            "system_audio": bool(self.system_var.get()),
            "system_volume": int(self.system_volume_var.get()),
            "mic_enabled": bool(self.mic_var.get()),
            "microphone": self.mic_device_var.get(),
            "mic_volume": int(self.mic_volume_var.get()),
            "webcam_enabled": bool(self.webcam_var.get()),
            "webcam": self.webcam_device_var.get(),
            "webcam_position": self.webcam_position_var.get(),
            "webcam_size": self.webcam_size_var.get(),
            "draw_mouse": bool(self.mouse_var.get()),
            "highlight_clicks": bool(self.clicks_var.get()),
            "countdown": self.countdown_var.get(),
            "auto_stop": bool(self.auto_stop_var.get()),
            "auto_stop_minutes": minutes,
            "open_editor": bool(self.open_editor_var.get()),
            "hotkey": self.hotkey_var.get(),
            "output_dir": str(self.output_dir) if self.output_dir != self.default_output_dir else "",
        }

    def _save(self) -> None:
        self.config_data = self._collect_settings()
        if self.save_callback is not None:
            try:
                self.save_callback(dict(self.config_data))
            except Exception:
                LOGGER.exception("Could not save recorder settings")

    # ------------------------------------------------------------------ layout
    def _monitor_labels(self) -> list[str]:
        if self.monitors:
            return [monitor.label for monitor in self.monitors]
        return [f"Screen 1: {self.winfo_screenwidth()}×{self.winfo_screenheight()}"]

    def _build(self) -> None:
        body = ttk.Frame(self, padding=native.PAD)
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # ---- capture area
        area = native.group(body, "Capture area", row=0, column=0, columnspan=2, sticky="ew")
        area.grid_columnconfigure(2, weight=1)
        ttk.Radiobutton(area, text="Full screen", value="fullscreen", variable=self.mode_var,
                        command=self._sync_mode).grid(row=0, column=0, sticky="w", pady=2)
        self.monitor_menu = native.combo(area, self.monitor_var, self._monitor_labels(), width=28)
        self.monitor_menu.grid(row=0, column=1, columnspan=2, sticky="w", padx=(native.GAP, 0), pady=2)
        ttk.Radiobutton(area, text="Select area", value="region", variable=self.mode_var,
                        command=self._region_mode_picked).grid(row=1, column=0, sticky="w", pady=2)
        region_row = ttk.Frame(area)
        region_row.grid(row=1, column=1, columnspan=2, sticky="w", padx=(native.GAP, 0), pady=2)
        self.select_button = ttk.Button(region_row, text="Select area…", command=self.select_region)
        self.select_button.pack(side="left")
        native.Text(region_row, "Ratio:").pack(side="left", padx=(native.GAP * 2, native.GAP))
        self.ratio_menu = native.combo(region_row, self.ratio_var, list(RATIOS), width=6)
        self.ratio_menu.pack(side="left")
        self.ratio_menu.bind("<<ComboboxSelected>>", lambda _e: self._ratio_changed())
        self.region_label = native.Text(region_row, "")
        self.region_label.pack(side="left", padx=(native.GAP * 2, 0))
        ttk.Radiobutton(area, text="Fixed size", value="fixed", variable=self.mode_var,
                        command=self._sync_mode).grid(row=2, column=0, sticky="w", pady=2)
        self.fixed_menu = native.combo(area, self.fixed_var, FIXED_SIZES, width=20)
        self.fixed_menu.grid(row=2, column=1, sticky="w", padx=(native.GAP, 0), pady=2)
        self.fixed_menu.bind("<<ComboboxSelected>>", lambda _e: self._fixed_changed())
        self.place_button = ttk.Button(area, text="Place…", command=self.select_region)
        self.place_button.grid(row=2, column=2, sticky="w", padx=(native.GAP, 0), pady=2)

        # ---- video
        video = native.group(body, "Video", row=1, column=0, sticky="nsew", pady=(native.PAD, 0),
                             padx=(0, native.GAP))
        rows = [
            ("Quality:", native.combo(video, self.quality_var, list(engine.QUALITIES), width=12)),
            ("Frame rate:", native.combo(video, self.fps_var, [str(v) for v in engine.FRAME_RATES], width=12)),
            ("Size:", native.combo(video, self.resolution_var, list(engine.RESOLUTIONS), width=12)),
            ("Format:", native.combo(video, self.container_var, engine.CONTAINERS, width=12)),
        ]
        self.encoder_menu = native.combo(video, self.encoder_var, ["Auto", "CPU (x264)"], width=18)
        rows.append(("Encoder:", self.encoder_menu))
        rows.append(("Capture:", native.combo(video, self.engine_var, list(CAPTURE_ENGINES), width=18)))
        for row, (label, widget) in enumerate(rows):
            native.Text(video, label).grid(row=row, column=0, sticky="w", pady=2)
            widget.grid(row=row, column=1, sticky="w", padx=(native.GAP, 0), pady=2)
        self.encoder_note = native.Text(video, "Checking GPU encoders…", wrap=px(230))
        self.encoder_note.configure(foreground=C["MUTED"])
        self.encoder_note.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=(2, 0))

        # ---- audio (each source has a live level meter, like Bandicam)
        audio = native.group(body, "Sound", row=1, column=1, sticky="nsew", pady=(native.PAD, 0))
        audio.grid_columnconfigure(1, weight=1)
        meter_bg = native.dialog_bg(self)
        self.system_check = ttk.Checkbutton(audio, text="System sound (what you hear)", variable=self.system_var,
                                            command=self._restart_monitors)
        self.system_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        native.Text(audio, "Volume:").grid(row=1, column=0, sticky="w")
        ttk.Scale(audio, from_=0, to=200, variable=self.system_volume_var, orient="horizontal").grid(
            row=1, column=1, sticky="ew", padx=(native.GAP, 0))
        native.Text(audio, "Level:").grid(row=2, column=0, sticky="w")
        self.system_meter = LevelMeter(audio, width=160, height=8, bg=meter_bg)
        self.system_meter.grid(row=2, column=1, sticky="ew", padx=(native.GAP, 0), pady=2)
        self.mic_check = ttk.Checkbutton(audio, text="Microphone", variable=self.mic_var,
                                         command=self._sync_audio)
        self.mic_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(native.GAP, 2))
        self.mic_menu = native.combo(audio, self.mic_device_var, [], width=28)
        self.mic_menu.grid(row=4, column=0, columnspan=2, sticky="ew", pady=2)
        self.mic_menu.bind("<<ComboboxSelected>>", lambda _e: self._restart_monitors(), add="+")
        native.Text(audio, "Volume:").grid(row=5, column=0, sticky="w")
        ttk.Scale(audio, from_=0, to=200, variable=self.mic_volume_var, orient="horizontal").grid(
            row=5, column=1, sticky="ew", padx=(native.GAP, 0))
        native.Text(audio, "Level:").grid(row=6, column=0, sticky="w")
        self.mic_meter = LevelMeter(audio, width=160, height=8, bg=meter_bg)
        self.mic_meter.grid(row=6, column=1, sticky="ew", padx=(native.GAP, 0), pady=2)
        self.meter_note = native.Text(audio, "", wrap=px(240))
        self.meter_note.configure(foreground=C["MUTED"])
        self.meter_note.grid(row=7, column=0, columnspan=2, sticky="ew")
        self.audio_note = native.Text(audio, "", wrap=px(240))
        self.audio_note.configure(foreground=C["MUTED"])
        self.audio_note.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        # ---- webcam and mouse
        extras = native.group(body, "Webcam and mouse", row=2, column=0, sticky="nsew", pady=(native.PAD, 0),
                              padx=(0, native.GAP))
        self.webcam_check = ttk.Checkbutton(extras, text="Show webcam in the video", variable=self.webcam_var,
                                            command=self._sync_webcam)
        self.webcam_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        self.webcam_menu = native.combo(extras, self.webcam_device_var, [], width=26)
        self.webcam_menu.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        self.webcam_position_menu = native.combo(extras, self.webcam_position_var, engine.WEBCAM_POSITIONS, width=12)
        self.webcam_position_menu.grid(row=2, column=0, sticky="w", pady=2)
        self.webcam_size_menu = native.combo(extras, self.webcam_size_var, list(engine.WEBCAM_SIZES), width=8)
        self.webcam_size_menu.grid(row=2, column=1, sticky="w", padx=(native.GAP, 0), pady=2)
        ttk.Checkbutton(extras, text="Show mouse cursor", variable=self.mouse_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(native.GAP, 2))
        self.clicks_check = ttk.Checkbutton(extras, text="Highlight mouse clicks", variable=self.clicks_var)
        self.clicks_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=2)

        # ---- recording options
        options = native.group(body, "Recording", row=2, column=1, sticky="nsew", pady=(native.PAD, 0))
        options.grid_columnconfigure(1, weight=1)
        native.Text(options, "Countdown:").grid(row=0, column=0, sticky="w", pady=2)
        native.combo(options, self.countdown_var, COUNTDOWNS, width=6).grid(row=0, column=1, sticky="w",
                                                                            padx=(native.GAP, 0), pady=2)
        stop_row = ttk.Frame(options)
        stop_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(stop_row, text="Stop after", variable=self.auto_stop_var).pack(side="left")
        ttk.Spinbox(stop_row, from_=1, to=600, width=5, textvariable=self.auto_stop_minutes_var).pack(
            side="left", padx=(native.GAP, native.GAP))
        native.Text(stop_row, "minutes").pack(side="left")
        ttk.Checkbutton(options, text="Open in the media editor when done", variable=self.open_editor_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2)
        native.Text(options, "Hotkey:").grid(row=3, column=0, sticky="w", pady=2)
        hotkey_menu = native.combo(options, self.hotkey_var, list(HOTKEYS), width=6)
        hotkey_menu.grid(row=3, column=1, sticky="w", padx=(native.GAP, 0), pady=2)
        hotkey_menu.bind("<<ComboboxSelected>>", lambda _e: self._register_hotkeys())
        self.hotkey_note = native.Text(options, "", wrap=px(240))
        self.hotkey_note.configure(foreground=C["MUTED"])
        self.hotkey_note.grid(row=4, column=0, columnspan=2, sticky="ew")
        native.Text(options, "Save to:").grid(row=5, column=0, sticky="nw", pady=(native.GAP, 0))
        self.folder_label = native.Text(options, self._short(self.output_dir), wrap=px(200))
        self.folder_label.grid(row=5, column=1, sticky="ew", padx=(native.GAP, 0), pady=(native.GAP, 0))
        folder_row = ttk.Frame(options)
        folder_row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Button(folder_row, text="Browse…", command=self.choose_folder).pack(side="left")
        ttk.Button(folder_row, text="Open folder", command=self.open_output_folder).pack(side="left",
                                                                                         padx=(native.GAP, 0))

        # ---- record button and status
        bottom = ttk.Frame(body)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(native.PAD, 0))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_label = native.Text(bottom, "Ready", wrap=px(420))
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.pause_button = ttk.Button(bottom, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=1, padx=(native.GAP, 0))
        self.record_button = ttk.Button(bottom, text="● Record", command=self.record_clicked, default="active")
        self.record_button.grid(row=0, column=2, padx=(native.GAP, 0), ipadx=px(10))
        self._update_region_label()
        self._sync_audio()
        self._sync_webcam()

    @staticmethod
    def _short(path: Path, limit: int = 40) -> str:
        text = str(path)
        return text if len(text) <= limit else f"…{text[-(limit - 1):]}"

    # ------------------------------------------------------------------ state of controls
    def _sync_mode(self) -> None:
        mode = self.mode_var.get()
        self.monitor_menu.configure(state="readonly" if mode == "fullscreen" else "disabled")
        self.select_button.configure(state="normal" if mode == "region" else "disabled")
        self.fixed_menu.configure(state="readonly" if mode == "fixed" else "disabled")
        self.place_button.configure(state="normal" if mode == "fixed" else "disabled")
        self._update_region_label()

    def _fixed_changed(self) -> None:
        self.region = None
        self._update_region_label()

    def _update_region_label(self) -> None:
        if not hasattr(self, "region_label"):
            return
        mode = self.mode_var.get()
        region = self._target_region(allow_none=True)
        if mode == "fullscreen":
            text = ""
        elif region:
            x, y, w, h = region
            text = f"{w}×{h} at {x}, {y}"
        else:
            text = "No area chosen yet"
        self.region_label.configure(text=text if mode == "region" else "")
        if mode == "fixed":
            self.place_button.configure(text=f"Place… ({text})" if region else "Place…")

    def _sync_audio(self) -> None:
        self.mic_menu.configure(state="readonly" if self.mic_var.get() and self.microphones else "disabled")
        self._restart_monitors()

    def _region_mode_picked(self) -> None:
        self._sync_mode()
        if self.region is None:
            self.select_region()

    def _ratio_changed(self) -> None:
        ratio = RATIOS.get(self.ratio_var.get())
        if ratio and self.region and self.mode_var.get() == "region":
            bounds = self._screen_bounds()
            bx, by, bw, bh = bounds
            x, y, w, h = self.region
            fitted = cg.fit_ratio((x - bx, y - by, w, h), ratio, bw, bh)
            self.region = engine.normalize_region((fitted[0] + bx, fitted[1] + by, fitted[2], fitted[3]), bounds)
            self._update_region_label()
        self._save()

    # ------------------------------------------------------------------ sound meters
    def _restart_monitors(self) -> None:
        """Live meters while the window is open, so the user sees that sound is picked up."""
        self._stop_monitors()
        if self._closing or self.state != "idle" or self.audio_error or not IS_WINDOWS:
            self._ensure_meter_loop()
            return
        if self.system_var.get():
            monitor = engine.LevelMonitor(loopback=True)
            monitor.start()
            self.monitors_live["system"] = monitor
        if self.mic_var.get() and self.microphones:
            monitor = engine.LevelMonitor(loopback=False, device=self.mic_device_var.get() or None)
            monitor.start()
            self.monitors_live["mic"] = monitor
        self._ensure_meter_loop()

    def _stop_monitors(self) -> None:
        for monitor in self.monitors_live.values():
            monitor.stop()
        self.monitors_live = {}

    def _ensure_meter_loop(self) -> None:
        if self._meter_id is None and not self._closing:
            self._meter_id = self.after(60, self._meter_tick)

    def _current_levels(self) -> dict[str, float]:
        volumes = {"system": self.system_volume_var.get() / 100.0, "mic": self.mic_volume_var.get() / 100.0}
        if self.session is not None and self.state in {"recording", "paused", "switching"}:
            raw = self.session.audio_levels() if self.state == "recording" else {}
        else:
            raw = {key: monitor.level for key, monitor in self.monitors_live.items()}
        return {key: min(1.0, value * volumes.get(key, 1.0)) for key, value in raw.items()}

    def _meter_tick(self) -> None:
        self._meter_id = None
        if self._closing:
            return
        try:
            levels = self._current_levels()
            if self.bar is not None:
                self.bar.update_levels(levels)
            self.system_meter.set(levels.get("system", 0.0))
            self.mic_meter.set(levels.get("mic", 0.0))
            errors = [f"{'Microphone' if key == 'mic' else 'System sound'}: {monitor.error}"
                      for key, monitor in self.monitors_live.items() if monitor.error]
            if self.mic_var.get() and "mic" in self.monitors_live and not errors:
                note = "Speak: the green bar should move." if levels.get("mic", 0.0) < 0.003 else "Microphone is working."
            else:
                note = ""
            self.meter_note.configure(text="\n".join(errors) or note)
        except tk.TclError:
            return
        self._meter_id = self.after(70, self._meter_tick)

    def _sync_webcam(self) -> None:
        state = "readonly" if self.webcam_var.get() and self.webcams else "disabled"
        for widget in (self.webcam_menu, self.webcam_position_menu, self.webcam_size_menu):
            widget.configure(state=state)

    # ------------------------------------------------------------------ device discovery
    def _probe_devices(self) -> None:
        audio_error = engine.audio_backend_error()
        microphones = [] if audio_error else engine.list_microphones()
        webcams = engine.list_webcams()
        self._post(self._apply_devices, audio_error, microphones, webcams)
        encoders = engine.available_encoders()
        self._post(self._apply_encoders, encoders)
        engine.ddagrab_available()  # cached now, so pressing Record never waits on this check

    def _apply_devices(self, audio_error: str, microphones: list[str], webcams: list[str]) -> None:
        self.audio_error = audio_error
        self.microphones = microphones
        self.webcams = webcams
        self.mic_menu.configure(values=microphones)
        if microphones and self.mic_device_var.get() not in microphones:
            self.mic_device_var.set(microphones[0])
        self.webcam_menu.configure(values=webcams)
        if webcams and self.webcam_device_var.get() not in webcams:
            self.webcam_device_var.set(webcams[0])
        if audio_error:
            self.system_var.set(False)
            self.mic_var.set(False)
            self.system_check.configure(state="disabled")
            self.mic_check.configure(state="disabled")
            self.audio_note.configure(text=f"Sound recording is unavailable: {audio_error}")
        elif not microphones:
            self.audio_note.configure(text="No microphone found.")
            self.mic_check.configure(state="disabled")
        else:
            self.audio_note.configure(text="")
        if not webcams:
            self.webcam_var.set(False)
            self.webcam_check.configure(state="disabled", text="Show webcam (no camera found)")
        self._sync_audio()
        self._sync_webcam()
        if not IS_WINDOWS:
            self.clicks_check.configure(state="disabled")
        self._restart_monitors()

    def _apply_encoders(self, encoders: list[str]) -> None:
        self.usable_encoders = encoders
        names = ["Auto"] + [name for name, key in engine.ENCODERS.items() if key in encoders]
        self.encoder_menu.configure(values=names)
        if self.encoder_var.get() not in names:
            self.encoder_var.set("Auto")
        gpu = [name for name, key in engine.ENCODERS.items() if key in encoders and key != "x264"]
        self.encoder_note.configure(text=("GPU encoding available: " + ", ".join(gpu)) if gpu
                                    else "No GPU encoder found; the CPU encoder is used.")

    # ------------------------------------------------------------------ hotkeys
    def _register_hotkeys(self) -> None:
        for hotkey in self.hotkeys:
            try:
                hotkey.stop()
            except Exception:
                pass
        self.hotkeys = []
        key = self.hotkey_var.get()
        if not IS_WINDOWS:
            self.hotkey_note.configure(text=f"{key} = start/stop, Shift+{key} = pause (while this window has focus)")
            self.bind_all_keys(key)
            return
        try:
            from hotkey import MOD_SHIFT, GlobalHotkey
        except Exception:
            return
        vk = HOTKEYS.get(key, 0x7B)
        failed = []
        for ident, modifiers, action in ((0x5245, 0, self.toggle_recording), (0x5246, MOD_SHIFT, self.toggle_pause)):
            hotkey = GlobalHotkey(lambda action=action: self._post(action), modifiers=modifiers, vk=vk,
                                  hotkey_id=ident)
            if hotkey.start():
                self.hotkeys.append(hotkey)
            else:
                failed.append(("Shift+" if modifiers else "") + key)
        if failed:
            self.hotkey_note.configure(text=f"{', '.join(failed)} is used by another program. Choose another key.")
        else:
            self.hotkey_note.configure(text=f"{key} = start/stop, Shift+{key} = pause/resume (works in any app)")
        self.bind_all_keys(key)

    def bind_all_keys(self, key: str) -> None:
        # Also inside this window (no global hotkey off Windows).
        for sequence in getattr(self, "_bound_keys", []):
            self.unbind(sequence)
        self._bound_keys = [f"<{key}>", f"<Shift-{key}>"]
        if not IS_WINDOWS:
            self.bind(f"<{key}>", lambda _e: self.toggle_recording())
            self.bind(f"<Shift-{key}>", lambda _e: self.toggle_pause())

    # ------------------------------------------------------------------ region
    def _screen_bounds(self) -> tuple[int, int, int, int]:
        bounds = engine.virtual_bounds(self.monitors)
        if bounds:
            return bounds
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _selected_monitor(self) -> engine.Monitor | None:
        labels = self._monitor_labels()
        if not self.monitors:
            return None
        try:
            return self.monitors[labels.index(self.monitor_var.get())]
        except ValueError:
            return self.monitors[0]

    def _fixed_size(self) -> tuple[int, int]:
        text = self.fixed_var.get().split(" ")[0]
        w, h = (int(v) for v in text.split("×"))
        return w, h

    def _target_region(self, allow_none: bool = False) -> tuple[int, int, int, int] | None:
        mode = self.mode_var.get()
        bounds = self._screen_bounds()
        if mode == "fullscreen":
            monitor = self._selected_monitor()
            rect = monitor.rect if monitor else bounds
            return engine.normalize_region(rect, bounds)
        if mode == "fixed":
            w, h = self._fixed_size()
            if self.region and (self.region[2], self.region[3]) == (engine.even(w), engine.even(h)):
                return engine.normalize_region(self.region, bounds)
            if allow_none:
                return None
            bx, by, bw, bh = bounds
            return engine.normalize_region((bx + (bw - w) // 2, by + (bh - h) // 2, w, h), bounds)
        if self.region:
            return engine.normalize_region(self.region, bounds)
        return None

    def select_region(self) -> None:
        """Show the area picker. Its ● REC button starts recording straight away."""
        if self.state != "idle" or getattr(self, "selector", None) is not None:
            return
        if self.mode_var.get() == "fullscreen":
            self.mode_var.set("region")
            self._sync_mode()
        fixed = self._fixed_size() if self.mode_var.get() == "fixed" else None
        self.state = "selecting"
        self.withdraw()

        def open_selector() -> None:
            try:
                self.selector = RegionSelector(
                    self, self._screen_bounds(), self.monitors, self._target_region(allow_none=True),
                    self._region_chosen, fixed_size=fixed, ratio=self.ratio_var.get(),
                    on_ratio_change=self.ratio_var.set)
            except Exception:
                LOGGER.exception("Area picker could not open")
                self.selector = None
                self.state = "idle"
                self.deiconify()

        self.after(150, open_selector)

    def _region_chosen(self, region: tuple[int, int, int, int] | None, action: str) -> None:
        self.selector = None
        self.state = "idle"
        if region:
            self.region = region
            self._update_region_label()
            self._save()
        if region and action == "record":
            # Straight into the recording: the window stays hidden.
            self.start_recording()
            return
        self.deiconify()
        self.lift()

    # ------------------------------------------------------------------ recording
    def record_clicked(self) -> None:
        """The Record button: full screen starts at once; an area is shown first so it can
        be checked or changed, with ● REC on its corner."""
        if self.state == "idle" and self.mode_var.get() in {"region", "fixed"}:
            self.select_region()
            return
        self.toggle_recording()

    def toggle_recording(self) -> None:
        if self.state == "selecting" and self.selector is not None:
            self.selector._finish("record")
            return
        if self.state == "idle":
            self.start_recording()
        elif self.state in {"recording", "paused"}:
            self.stop_recording()
        elif self.state == "countdown" and self.countdown is not None:
            self.countdown._finish(False)

    def start_recording(self) -> None:
        if self.state != "idle" or self._closing:
            return
        region = self._target_region()
        if region is None:
            self.select_region()
            return
        self._save()
        self._stop_monitors()
        self.region_to_record = region
        previous = foreground_window()
        self.previous_foreground = previous if previous != _wrapper_hwnd(self) else 0
        seconds = {"Off": 0, "3 s": 3, "5 s": 5, "10 s": 10}.get(self.countdown_var.get(), 0)
        self.withdraw()
        if seconds:
            self.state = "countdown"
            self.countdown = Countdown(self, region, seconds, self._countdown_done)
        else:
            self.after(200, lambda: self._countdown_done(True))

    def _countdown_done(self, go: bool) -> None:
        self.countdown = None
        if not go:
            self.state = "idle"
            self.deiconify()
            self.status_label.configure(text="Recording cancelled")
            self._restart_monitors()
            return
        region = self.region_to_record
        monitor = engine.monitor_for_region(self.monitors, region)
        try:
            minutes = max(1, int(float(self.auto_stop_minutes_var.get())))
        except ValueError:
            minutes = 10
        self.auto_stop_seconds = minutes * 60 if self.auto_stop_var.get() else None
        settings = engine.RecordSettings(
            region=region,
            output_dir=self.output_dir,
            fps=int(self.fps_var.get()),
            quality=self.quality_var.get(),
            resolution=self.resolution_var.get(),
            encoder=engine.ENCODERS.get(self.encoder_var.get(), "auto"),
            container=self.container_var.get(),
            draw_mouse=bool(self.mouse_var.get()),
            system_audio=bool(self.system_var.get()),
            microphone=(self.mic_device_var.get() or None) if self.mic_var.get() else None,
            system_volume=self.system_volume_var.get() / 100.0,
            mic_volume=self.mic_volume_var.get() / 100.0,
            webcam=(self.webcam_device_var.get() or None) if self.webcam_var.get() else None,
            webcam_position=self.webcam_position_var.get(),
            webcam_size=self.webcam_size_var.get(),
            capture_engine=CAPTURE_ENGINES.get(self.engine_var.get(), "auto"),
            monitor_index=monitor.index if monitor else None,
            monitor_origin=(monitor.left, monitor.top) if monitor else (0, 0),
        )
        try:
            session = engine.RecordingSession(settings, usable_encoders=self.usable_encoders, log=LOGGER.info)
        except Exception as exc:
            self._recording_failed(str(exc))
            return
        self.session = session
        self.state = "starting"
        threading.Thread(target=self._start_worker, args=(session,), daemon=True).start()

    def _start_worker(self, session: engine.RecordingSession) -> None:
        try:
            session.start()
            self._post(self._recording_started, session)
        except Exception as exc:
            LOGGER.exception("Recording could not start")
            self._post(self._recording_failed, str(exc))

    def _recording_started(self, session: engine.RecordingSession) -> None:
        if session is not self.session:
            return
        self.state = "recording"
        region = self.region_to_record
        LOGGER.info("Recording started region=%s engine=%s encoder=%s output=%s", region, session.engine,
                    session.encoder, session.output)
        bounds = self._screen_bounds()
        if region != engine.normalize_region(bounds, bounds) and not self._is_full_monitor(region):
            self.border = RegionBorder(self, region)
        monitor = engine.monitor_for_region(self.monitors, region)
        self.bar = ControlBar(self, region, monitor.rect if monitor else bounds,
                              show_mic=any(not rec.loopback for rec, _v in session.parts[-1].audio) if session.parts
                              else False,
                              show_system=any(rec.loopback for rec, _v in session.parts[-1].audio) if session.parts
                              else False)
        if self.clicks_var.get():
            self.highlighter = ClickHighlighter(self)
            self.highlighter.start()
        # The overlays above may have taken the focus: hand it back to the user's app.
        self.after(50, lambda: restore_foreground(getattr(self, "previous_foreground", 0)))
        self.record_button.configure(text="■ Stop")
        self.pause_button.configure(state="normal", text="Pause")
        self.status_label.configure(text=f"Recording… ({session.engine}, {session.encoder})")
        for warning in session.warnings:
            self.status_label.configure(text=warning)
        self._tick()
        self._ensure_meter_loop()

    def _is_full_monitor(self, region: tuple[int, int, int, int]) -> bool:
        return any(region == engine.normalize_region(m.rect) for m in self.monitors)

    def _tick(self) -> None:
        self._tick_id = None
        session = self.session
        if session is None or self.state not in {"recording", "paused"}:
            return
        elapsed = session.elapsed
        if self.bar is not None:
            self.bar.update_status(elapsed, session.size, self.state == "paused")
        if self.auto_stop_seconds and elapsed >= self.auto_stop_seconds and self.state == "recording":
            self.status_label.configure(text="Time limit reached")
            self.stop_recording()
            return
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 20 == 0 and self.state == "recording":  # every 10 s
            free = session.free_space()
            if free is not None and free < 200 * 1024 * 1024:
                self.status_label.configure(text="The disk is almost full: the recording was stopped and saved.")
                session.warnings.append("Stopped early because the disk was almost full.")
                self.stop_recording()
                return
        if session.parts and self.state == "recording":
            process = session.parts[-1].process
            if process is not None and process.poll() is not None:
                self.status_label.configure(text="The capture stopped unexpectedly; saving what was recorded.")
                self.stop_recording()
                return
        self._tick_id = self.after(500, self._tick)

    def toggle_pause(self) -> None:
        session = self.session
        if session is None or self.state not in {"recording", "paused"}:
            return
        action = session.pause if self.state == "recording" else session.resume
        target = "paused" if self.state == "recording" else "recording"
        self.state = "switching"

        def worker() -> None:
            try:
                action()
                self._post(self._pause_switched, target, "")
            except Exception as exc:
                self._post(self._pause_switched, "paused", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _pause_switched(self, state: str, error: str) -> None:
        self.state = state
        if error:
            self.status_label.configure(text=f"Could not resume: {error[-200:]}")
        paused = state == "paused"
        self.pause_button.configure(text="Resume" if paused else "Pause")
        if self.highlighter is not None:
            if paused:
                self.highlighter.stop()
            elif self.clicks_var.get():
                self.highlighter.start()
        if self.bar is not None and self.session is not None:
            self.bar.update_status(self.session.elapsed, self.session.size, paused)
        if self._tick_id is None:
            self._tick()

    def stop_recording(self) -> None:
        session = self.session
        if session is None or self.state not in {"recording", "paused"}:
            return
        self.state = "finishing"
        if self.highlighter is not None:
            self.highlighter.destroy()
            self.highlighter = None
        if self.border is not None:
            self.border.destroy()
            self.border = None
        if self.bar is not None:
            self.bar.set_finishing()
        self.record_button.configure(state="disabled", text="Saving…")
        self.pause_button.configure(state="disabled")
        self.status_label.configure(text="Saving the recording…")

        def worker() -> None:
            try:
                output = session.stop()
                self._post(self._recording_saved, session, output)
            except Exception as exc:
                LOGGER.exception("Recording could not be saved")
                self._post(self._recording_failed, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_ui(self) -> None:
        self.state = "idle"
        self.session = None
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except tk.TclError:
                pass
            self._tick_id = None
        for attr in ("bar", "border", "highlighter"):
            item = getattr(self, attr)
            if item is not None:
                try:
                    item.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._closing:
            return
        self.record_button.configure(state="normal", text="● Record")
        self.pause_button.configure(state="disabled", text="Pause")
        self.deiconify()
        self.lift()
        self._restart_monitors()

    def _recording_saved(self, session: engine.RecordingSession, output: Path) -> None:
        LOGGER.info("Recording saved output=%s duration=%.1f", output, session.elapsed)
        self._reset_ui()
        note = f"Saved {output.name} ({engine.format_duration(session.elapsed)}, " \
               f"{engine.format_size(output.stat().st_size if output.exists() else 0)})"
        if session.warnings:
            note += "\n" + "\n".join(dict.fromkeys(session.warnings))
        if self._closing:
            return
        self.status_label.configure(text=note)
        try:
            self.parent.last_file = output
        except Exception:
            pass
        if self.open_editor_var.get() and self.open_in_editor is not None:
            self.open_in_editor(output)
        elif messagebox.askyesno(APP_TITLE, f"{note}\n\nShow the file in its folder?", parent=self):
            _open_folder(output)

    def _recording_failed(self, error: str) -> None:
        self._reset_ui()
        if self._closing:
            return
        self.status_label.configure(text="Recording failed")
        messagebox.showerror(APP_TITLE, f"The recording failed.\n\n{error[-1500:]}", parent=self)

    # ------------------------------------------------------------------ folders and recovery
    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Save recordings to", initialdir=str(self.output_dir), parent=self)
        if selected:
            self.output_dir = Path(selected)
            self.folder_label.configure(text=self._short(self.output_dir))
            self._save()

    def open_output_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(str(self.output_dir))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(self.output_dir)])

    def _offer_recovery(self) -> None:
        if self._closing or self.state != "idle":
            return
        leftovers = engine.recover_unfinished(self.output_dir)
        if not leftovers:
            return
        folder = leftovers[0]
        if not messagebox.askyesno(
                APP_TITLE,
                "A recording was interrupted (the app closed or the PC turned off while recording).\n\n"
                "Save what was recorded?", parent=self):
            return
        self.status_label.configure(text="Saving the interrupted recording…")

        def worker() -> None:
            try:
                output = engine.finish_unfinished(folder, self.container_var.get())
                self._post(lambda: (self.status_label.configure(text=f"Recovered {output.name}"),
                                    _open_folder(output)))
            except Exception as exc:
                message = str(exc)
                self._post(lambda: messagebox.showerror(APP_TITLE, message, parent=self))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ plumbing
    def _post(self, callback: Callable[..., Any], *args: Any) -> None:
        self.ui_queue.put((callback, args))

    def _drain_queue(self) -> None:
        try:
            while True:
                callback, args = self.ui_queue.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    LOGGER.exception("Recorder UI callback failed")
        except queue.Empty:
            pass
        if not self._closing or self.session is not None:
            try:
                self.after(40, self._drain_queue)
            except tk.TclError:
                pass

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def close(self) -> None:
        if self.state in {"recording", "paused", "starting", "switching"}:
            if not messagebox.askyesno(APP_TITLE, "A recording is running. Stop and save it, then close?",
                                       parent=self):
                return
            self.stop_recording()
            self.after(300, self._close_when_done)
            return
        if self.state == "finishing":
            self.after(300, self._close_when_done)
            return
        self._destroy()

    def _on_destroyed(self, event: Any) -> None:
        # The app can quit while a recording runs (tray > Quit): close the capture properly.
        if event.widget is not self:
            return
        self._closing = True
        self._stop_monitors()
        for hotkey in self.hotkeys:
            try:
                hotkey.stop()
            except Exception:
                pass
        self.hotkeys = []
        session = self.session
        if session is not None and session.state in {"recording", "paused"}:
            session.abort()

    def _close_when_done(self) -> None:
        if self.state in {"finishing", "switching", "starting"}:
            self.after(300, self._close_when_done)
            return
        self._destroy()

    def _destroy(self) -> None:
        self._closing = True
        self._stop_monitors()
        self._save()
        for hotkey in self.hotkeys:
            try:
                hotkey.stop()
            except Exception:
                pass
        self.hotkeys = []
        if self.session is not None and self.state in {"recording", "paused"}:
            self.session.abort()
        self._reset_ui()
        self.destroy()
