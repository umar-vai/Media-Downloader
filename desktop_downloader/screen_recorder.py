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
DEFAULTS: dict[str, Any] = {
    "mode": "fullscreen",            # fullscreen, region, fixed
    "monitor": 0,
    "region": None,                  # [x, y, w, h]
    "fixed_size": FIXED_SIZES[1],
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


# --------------------------------------------------------------------------- overlays
class RegionSelector(tk.Toplevel):
    """Dimmed full-desktop overlay: drag a box, move or resize it, Enter to confirm.

    A click without dragging picks the whole screen under the mouse.
    """

    def __init__(self, master: tk.Misc, bounds: tuple[int, int, int, int], monitors: list[engine.Monitor],
                 initial: tuple[int, int, int, int] | None, on_done: Callable[[tuple[int, int, int, int] | None],
                                                                              None],
                 *, fixed_size: tuple[int, int] | None = None) -> None:
        super().__init__(master)
        self.bounds = bounds
        self.monitors = monitors
        self.on_done = on_done
        self.fixed_size = fixed_size
        bx, by, bw, bh = bounds
        self.overrideredirect(True)
        self.geometry(f"{bw}x{bh}+{bx}+{by}")
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.55)
        except tk.TclError:
            pass
        self.configure(bg="black", cursor="crosshair")
        self.canvas = tk.Canvas(self, bg="#101010", highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.rect: cg.Rect | None = None
        if initial:
            x, y, w, h = initial
            self.rect = (x - bx, y - by, w, h)
        elif fixed_size:
            w, h = fixed_size
            self.rect = ((bw - w) / 2, (bh - h) / 2, w, h)
        self.drag: dict[str, Any] | None = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._finish(True))
        self.bind("<Return>", lambda _e: self._finish(True))
        self.bind("<KP_Enter>", lambda _e: self._finish(True))
        self.bind("<Escape>", lambda _e: self._finish(False))
        self.bind("<Button-3>", lambda _e: self._finish(False))
        for key, (dx, dy) in {"<Left>": (-1, 0), "<Right>": (1, 0), "<Up>": (0, -1), "<Down>": (0, 1)}.items():
            self.bind(key, lambda _e, dx=dx, dy=dy: self._nudge(dx, dy))
            self.bind(f"<Shift-{key[1:]}", lambda _e, dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))
        self.after(30, self._grab)
        self._draw()

    def _grab(self) -> None:
        try:
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.bounds[2], self.bounds[3]

    def _press(self, event: Any) -> None:
        fw, fh = self.frame_size
        handle = cg.hit_test(self.rect, event.x, event.y, 10) if self.rect else None
        if self.fixed_size and handle not in (None, "move"):
            handle = "move"
        if handle:
            self.drag = {"mode": handle, "x": event.x, "y": event.y, "rect": self.rect, "moved": False}
        elif self.fixed_size:
            w, h = self.fixed_size
            self.rect = cg.keep_inside((event.x - w / 2, event.y - h / 2, w, h), fw, fh)
            self.drag = {"mode": "move", "x": event.x, "y": event.y, "rect": self.rect, "moved": True}
        else:
            self.drag = {"mode": "new", "x": event.x, "y": event.y, "rect": self.rect, "moved": False}
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
                self.rect = cg.from_points(self.drag["x"], self.drag["y"], event.x, event.y, fw, fh)
        else:
            self.rect = cg.resize(self.drag["rect"], self.drag["mode"], dx, dy, fw, fh,
                                  min_size=engine.MIN_REGION)
        self._draw()

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
        if self.rect and (self.rect[2] < engine.MIN_REGION or self.rect[3] < engine.MIN_REGION):
            self.rect = None
        self._draw()

    def _hover(self, event: Any) -> None:
        handle = cg.hit_test(self.rect, event.x, event.y, 10) if self.rect else None
        cursors = {"move": "fleur", "n": "top_side", "s": "bottom_side", "e": "right_side", "w": "left_side",
                   "nw": "top_left_corner", "ne": "top_right_corner", "sw": "bottom_left_corner",
                   "se": "bottom_right_corner"}
        if self.fixed_size and handle:
            handle = "move"
        self.canvas.configure(cursor=cursors.get(handle or "", "crosshair"))

    def _nudge(self, dx: int, dy: int) -> None:
        if self.rect:
            self.rect = cg.move(self.rect, dx, dy, *self.frame_size)
            self._draw()

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        fw, fh = self.frame_size
        text = ("Drag to select the area to record • click a screen for all of it\n"
                "Enter or double-click = OK • Esc or right-click = cancel • arrows move the box")
        if self.fixed_size:
            text = "Move the box to the area to record • Enter or double-click = OK • Esc = cancel"
        if self.rect:
            x, y, w, h = self.rect
            # A light dotted tint shows the chosen area (Tk on Windows draws no stipple: border only).
            tint = {} if IS_WINDOWS else {"fill": "#FFFFFF", "stipple": "gray12"}
            canvas.create_rectangle(x, y, x + w, y + h, outline=BORDER_COLOR, width=3, **tint)
            for hx, hy in ((x, y), (x + w / 2, y), (x + w, y), (x + w, y + h / 2), (x + w, y + h),
                           (x + w / 2, y + h), (x, y + h), (x, y + h / 2)):
                canvas.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill=BORDER_COLOR, outline="white")
            ex, ey, ew, eh = engine.normalize_region((x, y, w, h))
            label = f"{ew} × {eh}"
            ly = y - 26 if y > 40 else y + h + 8
            canvas.create_rectangle(x, ly, x + 12 + 9 * len(label), ly + 22, fill="black", outline=BORDER_COLOR)
            canvas.create_text(x + 6, ly + 11, text=label, fill="white", anchor="w", font=("Segoe UI", 11, "bold"))
        canvas.create_text(fw / 2, 40, text=text, fill="white", font=("Segoe UI", 13, "bold"), justify="center")

    def _finish(self, accept: bool) -> None:
        result = None
        if accept and self.rect:
            bx, by, _bw, _bh = self.bounds
            x, y, w, h = self.rect
            result = engine.normalize_region((x + bx, y + by, w, h), self.bounds)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_done(result)


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
    """Shows a coloured ring where the mouse is clicked (yellow = left, red = right).

    The ring is a small click-through window, so it is part of the screen and gets
    recorded like everything else; nothing has to be added to the video afterwards.
    """

    SIZE = 56

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
        self._hide_after: str | None = None
        self.active = False

    def start(self) -> None:
        if not IS_WINDOWS or self.active:
            return
        self.active = True
        self._poll()

    def stop(self) -> None:
        self.active = False
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

    def _poll(self) -> None:
        if not self.active:
            return
        try:
            left, right, x, y = _mouse_state()
            if left and not self._state[0]:
                self._show(x, y, RING_COLORS["left"])
            elif right and not self._state[1]:
                self._show(x, y, RING_COLORS["right"])
            self._state = (left, right)
        except Exception:
            pass
        self._after = self.master.after(15, self._poll)

    def _show(self, x: int, y: int, color: str) -> None:
        size = self.SIZE
        self.canvas.delete("all")
        self.canvas.create_oval(4, 4, size - 4, size - 4, outline=color, width=5)
        self.canvas.create_oval(size / 2 - 5, size / 2 - 5, size / 2 + 5, size / 2 + 5, fill=color, outline=color)
        self.canvas.update_idletasks()
        if not show_without_focus(self.window, x - size // 2, y - size // 2, size, size):
            self.window.geometry(f"{size}x{size}+{x - size // 2}+{y - size // 2}")
            self.window.deiconify()
            self.window.lift()
        if self._hide_after:
            try:
                self.master.after_cancel(self._hide_after)
            except tk.TclError:
                pass
        self._hide_after = self.master.after(380, self._hide)


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
    """Small always-on-top bar while recording: time, size, pause, stop."""

    def __init__(self, master: "ScreenRecorderWindow", region: tuple[int, int, int, int],
                 screen: tuple[int, int, int, int]) -> None:
        super().__init__(master)
        self.recorder = master
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#202020")
        pad = {"padx": px(4), "pady": px(4)}
        self.dot = tk.Label(self, text="●", fg="#FF3B30", bg="#202020", font=("Segoe UI", 12, "bold"))
        self.dot.pack(side="left", **pad)
        self.time_label = tk.Label(self, text="00:00:00", fg="white", bg="#202020", font=("Consolas", 12, "bold"))
        self.time_label.pack(side="left", **pad)
        self.size_label = tk.Label(self, text="0 KB", fg="#BBBBBB", bg="#202020", font=("Segoe UI", 9))
        self.size_label.pack(side="left", **pad)
        self.pause_button = tk.Button(self, text="❚❚ Pause", command=master.toggle_pause, relief="flat",
                                      bg="#3A3A3A", fg="white", activebackground="#555555", activeforeground="white",
                                      bd=0, padx=px(8))
        self.pause_button.pack(side="left", **pad)
        self.stop_button = tk.Button(self, text="■ Stop", command=master.stop_recording, relief="flat",
                                     bg="#C42B1C", fg="white", activebackground="#E81123", activeforeground="white",
                                     bd=0, padx=px(8))
        self.stop_button.pack(side="left", **pad)
        grip = tk.Label(self, text="⠿", fg="#888888", bg="#202020", cursor="fleur")
        grip.pack(side="left", padx=(0, px(4)))
        grip.bind("<ButtonPress-1>", self._start_move)
        grip.bind("<B1-Motion>", self._move)
        self.update_idletasks()
        self._place(region, screen)
        exclude_from_capture(self)
        self._blink = True

    def _place(self, region: tuple[int, int, int, int], screen: tuple[int, int, int, int]) -> None:
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x, y, w, h = region
        sx, sy, sw, sh = screen
        # Above the area if there is room, else below it, else inside the top edge.
        left = max(sx, min(x + w - width, sx + sw - width))
        if y - height - 6 >= sy:
            top = y - height - 6
        elif y + h + 6 + height <= sy + sh:
            top = y + h + 6
        else:
            top = y + 6
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
        self.dot.configure(text="❚❚" if paused else "●", fg="#FFC400" if paused else
                           ("#FF3B30" if self._blink else "#7A1A14"))
        self.pause_button.configure(text="● Resume" if paused else "❚❚ Pause")

    def set_finishing(self) -> None:
        self.dot.configure(text="…", fg="#BBBBBB")
        self.pause_button.configure(state="disabled")
        self.stop_button.configure(state="disabled", text="Saving…")


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
                        command=self._sync_mode).grid(row=1, column=0, sticky="w", pady=2)
        self.select_button = ttk.Button(area, text="Select area…", command=self.select_region)
        self.select_button.grid(row=1, column=1, sticky="w", padx=(native.GAP, 0), pady=2)
        self.region_label = native.Text(area, "")
        self.region_label.grid(row=1, column=2, sticky="w", padx=(native.GAP, 0))
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

        # ---- audio
        audio = native.group(body, "Sound", row=1, column=1, sticky="nsew", pady=(native.PAD, 0))
        audio.grid_columnconfigure(1, weight=1)
        self.system_check = ttk.Checkbutton(audio, text="System sound (what you hear)", variable=self.system_var)
        self.system_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        native.Text(audio, "Volume:").grid(row=1, column=0, sticky="w")
        ttk.Scale(audio, from_=0, to=200, variable=self.system_volume_var, orient="horizontal").grid(
            row=1, column=1, sticky="ew", padx=(native.GAP, 0))
        self.mic_check = ttk.Checkbutton(audio, text="Microphone", variable=self.mic_var,
                                         command=self._sync_audio)
        self.mic_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(native.GAP, 2))
        self.mic_menu = native.combo(audio, self.mic_device_var, [], width=28)
        self.mic_menu.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        native.Text(audio, "Volume:").grid(row=4, column=0, sticky="w")
        ttk.Scale(audio, from_=0, to=200, variable=self.mic_volume_var, orient="horizontal").grid(
            row=4, column=1, sticky="ew", padx=(native.GAP, 0))
        self.audio_note = native.Text(audio, "", wrap=px(240))
        self.audio_note.configure(foreground=C["MUTED"])
        self.audio_note.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 0))

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
        self.record_button = ttk.Button(bottom, text="● Record", command=self.toggle_recording, default="active")
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

    def select_region(self, then_record: bool = False) -> None:
        if self.state != "idle":
            return
        fixed = self._fixed_size() if self.mode_var.get() == "fixed" else None
        self.withdraw()
        self.after(150, lambda: RegionSelector(
            self, self._screen_bounds(), self.monitors, self._target_region(allow_none=True),
            lambda region: self._region_chosen(region, then_record), fixed_size=fixed))

    def _region_chosen(self, region: tuple[int, int, int, int] | None, then_record: bool) -> None:
        self.deiconify()
        if region:
            self.region = region
            self._update_region_label()
            self._save()
            if then_record:
                self.toggle_recording()

    # ------------------------------------------------------------------ recording
    def toggle_recording(self) -> None:
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
            self.select_region(then_record=True)
            return
        self._save()
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
        self.bar = ControlBar(self, region, monitor.rect if monitor else bounds)
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
