from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from PIL import Image, ImageEnhance, ImageTk
from tkinter import filedialog, messagebox, ttk

import crop_geometry as cg
import native
from app_logging import get_logger
from editor_history import EditorHistory, EditorSnapshot
from media_editor_engine import (
    CROP_PRESETS,
    CREATE_NO_WINDOW,
    RESOLUTIONS,
    MediaInfo,
    build_export_command,
    build_frame_export_command,
    build_player_filters,
    build_preview_clip_command,
    build_video_filters,
    compute_crop,
    crop_ratio,
    output_dimensions,
    extract_preview_frame,
    extract_waveform,
    format_time,
    parse_time,
    probe_media,
    safe_export_name,
)
from media_editor_widgets import TimelineCanvas
from media_player import EmbeddedMediaPlayer, PlayerUnavailableError
from theme import C
from ui_kit import px


APP_TITLE = "Media Editor"

# Classic Windows look: the system colours from the app theme. Only problems are coloured;
# progress and success messages are plain text, as in a Windows status bar.
BG = C["BG"]
SURFACE = C["SURFACE"]
TEXT = C["TEXT"]
MUTED = C["MUTED"]
DANGER = C["DANGER"]
SUCCESS = TEXT
WARNING = TEXT
# Kept for the playback code, which still passes button colours (ignored by EButton).
CYAN = CYAN_HOVER = PURPLE = PURPLE_HOVER = ""
PREVIEW_BG = "#000000"  # the video preview stays black, like any media player

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}
SPEEDS = ["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x", "3.0x", "4.0x"]
ROTATIONS = ["0°", "90°", "180°", "270°"]
CROP_FRAME_SIZE = (1280, 720)  # still frame shown while the crop box is adjusted
CROP_CURSORS = {
    "move": "fleur",
    "n": "top_side",
    "s": "bottom_side",
    "e": "right_side",
    "w": "left_side",
    "nw": "top_left_corner",
    "ne": "top_right_corner",
    "sw": "bottom_left_corner",
    "se": "bottom_right_corner",
}

LOGGER = get_logger("editor")


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _short_path(path: Path, limit: int = 52) -> str:
    text = str(path)
    if len(text) <= limit:
        return text
    keep = max(12, (limit - 3) // 2)
    return f"{text[:keep]}…{text[-keep:]}"


# --------------------------------------------------------------------------- native adapters
# The editing code below was written against CustomTkinter widgets. These thin ttk wrappers
# accept the same calls (colour keywords are ignored) so that code did not have to change.
class EButton(ttk.Button):
    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        for key in ("fg_color", "hover_color", "text_color", "border_color", "font"):
            kw.pop(key, None)
        if cnf is None and not kw:
            return super().configure()
        return super().configure(cnf, **kw)

    config = configure  # type: ignore[assignment]


class Choice(ttk.Combobox):
    """A read-only drop-down list that behaves like CTkOptionMenu (command gets the value)."""

    def __init__(self, master: tk.Misc, variable: tk.Variable, values: list[str],
                 command: Callable[[str], Any] | None = None, width: int = 12) -> None:
        super().__init__(master, textvariable=variable, values=values, state="readonly", width=width)
        self._command = command
        self.bind("<<ComboboxSelected>>", self._selected, add="+")

    def _selected(self, _event: Any = None) -> None:
        self.selection_clear()
        if self._command is not None:
            self._command(self.get())

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if kw.get("state") == "normal":
            kw["state"] = "readonly"  # never editable: pick from the list only
        if cnf is None and not kw:
            return super().configure()
        return super().configure(cnf, **kw)

    config = configure  # type: ignore[assignment]


class Tabs(ttk.Notebook):
    """ttk tabs with CTkTabview's ``set(name)``."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._pages: dict[str, ttk.Frame] = {}

    def page(self, name: str) -> ttk.Frame:
        frame = ttk.Frame(self, padding=(native.PAD, native.PAD, native.PAD, native.PAD))
        frame.grid_columnconfigure(0, weight=1)
        self.add(frame, text=name)
        self._pages[name] = frame
        return frame

    def set(self, name: str) -> None:
        if name in self._pages:
            self.select(self._pages[name])


class Progress(native.Progress):
    """ttk progress bar with CTkProgressBar's ``set``/``start``/``stop``."""

    def start(self, interval: int = 20) -> None:  # type: ignore[override]
        super().start(interval)


class MediaEditorWindow(tk.Toplevel):
    def __init__(self, parent: Any, source_path: Path, output_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.source_path = Path(source_path)
        self.output_dir = Path(output_dir or self.source_path.parent)
        self.info: MediaInfo | None = None

        self.preview_pil: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_generation = 0
        self.preview_after_id: str | None = None
        self.preview_lock = threading.Lock()
        self.thumbnail_generation = 0

        self.player: EmbeddedMediaPlayer | None = None
        self.playing = False
        self.player_tick_id: str | None = None
        self.player_rate_supported = True

        self.export_process: subprocess.Popen[str] | None = None
        self.export_cancelled = False
        self.preview_clip_busy = False
        self.render_busy = False
        self.last_export: Path | None = None
        self.ui_queue: queue.Queue[tuple[Any, tuple[Any, ...]]] = queue.Queue()
        self._closing = False
        self.history = EditorHistory(limit=60)
        self._applying_history = False

        self.temp_dir = Path(tempfile.gettempdir()) / "MediaDownloaderEditor"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.start_var = tk.StringVar(self, value="00:00.000")
        self.end_var = tk.StringVar(self, value="")
        self.playhead_var = tk.DoubleVar(self, value=0.0)

        self.crop_var = tk.StringVar(self, value="Original")
        self.rotate_var = tk.StringVar(self, value="0°")
        self.speed_var = tk.StringVar(self, value="1.0x")
        self.custom_x_var = tk.StringVar(self, value="0")
        self.custom_y_var = tk.StringVar(self, value="0")
        self.custom_w_var = tk.StringVar(self, value="")
        self.custom_h_var = tk.StringVar(self, value="")
        self.flip_h_var = tk.BooleanVar(self, value=False)
        self.flip_v_var = tk.BooleanVar(self, value=False)
        self.brightness_var = tk.DoubleVar(self, value=0.0)
        self.contrast_var = tk.DoubleVar(self, value=0.0)
        self.saturation_var = tk.DoubleVar(self, value=0.0)
        self.resolution_var = tk.StringVar(self, value="Original")

        # On-video crop box. While it is shown the player is hidden and a still frame of the
        # whole source is drawn on the preview canvas, with the box on top.
        self.crop_mode = False
        self.crop_rect: cg.Rect | None = None
        self.crop_drag: dict[str, Any] | None = None
        self.crop_saved: tuple[str, str, str, str, str] | None = None
        self.crop_frame_pil: Image.Image | None = None
        self.crop_frame_photo: ImageTk.PhotoImage | None = None
        self.crop_frame_photo_size: tuple[int, int] = (0, 0)
        self.crop_frame_scaled: Image.Image | None = None
        self.crop_bright_photo: ImageTk.PhotoImage | None = None
        self.crop_frame_generation = 0
        self.crop_frame_after_id: str | None = None
        self.crop_view: tuple[float, float, float] | None = None  # offset x, offset y, scale
        self.color_after_id: str | None = None

        self.mute_var = tk.BooleanVar(self, value=False)
        self.volume_var = tk.DoubleVar(self, value=100.0)
        self.fade_in_var = tk.StringVar(self, value="0")
        self.fade_out_var = tk.StringVar(self, value="0")

        self.output_name_var = tk.StringVar(self, value=f"{self.source_path.stem}_edited")
        self.format_var = tk.StringVar(self, value="MP4")
        self.quality_var = tk.StringVar(self, value="High")

        self.title(f"{self.source_path.name} - {APP_TITLE}")
        self.configure(bg=native.dialog_bg(self))
        try:
            icon = getattr(parent, "_icon_file", None)
            if icon:
                self.iconbitmap(icon)
        except tk.TclError:
            pass
        # Fit the screen (work area) at any display scaling.
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(px(1100), screen_w - 40), min(px(720), screen_h - 90)
        self.geometry(f"{width}x{height}")
        self.minsize(min(px(900), width), min(px(600), height))
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_ui()
        self.bind("<space>", self._on_space)
        self.bind("<KeyPress-i>", lambda event: self._on_mark_shortcut(event, "in"))
        self.bind("<KeyPress-o>", lambda event: self._on_mark_shortcut(event, "out"))
        self.bind("<Left>", lambda event: self._on_arrow_shortcut(event, -1, False))
        self.bind("<Right>", lambda event: self._on_arrow_shortcut(event, 1, False))
        self.bind("<Shift-Left>", lambda event: self._on_arrow_shortcut(event, -1, True))
        self.bind("<Shift-Right>", lambda event: self._on_arrow_shortcut(event, 1, True))
        self.bind("<Control-z>", lambda event: self._on_history_shortcut(event, "undo"))
        self.bind("<Control-y>", lambda event: self._on_history_shortcut(event, "redo"))
        self.bind("<Control-Shift-Z>", lambda event: self._on_history_shortcut(event, "redo"))
        self.bind("<Up>", lambda event: self._on_vertical_arrow(event, -1, False))
        self.bind("<Down>", lambda event: self._on_vertical_arrow(event, 1, False))
        self.bind("<Shift-Up>", lambda event: self._on_vertical_arrow(event, -1, True))
        self.bind("<Shift-Down>", lambda event: self._on_vertical_arrow(event, 1, True))
        self.bind("<Return>", self._on_crop_return)
        self.bind("<Escape>", self._on_crop_escape)
        self.bind("<KeyPress-c>", self._on_crop_shortcut)
        self.after(40, self._drain_ui_queue)
        self.after(80, self._start_load)

    def _post_ui(self, callback: Any, *args: Any) -> None:
        if not self._closing:
            self.ui_queue.put((callback, args))

    def _drain_ui_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                callback, args = self.ui_queue.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        except Exception as exc:
            try:
                self.status_label.configure(text=f"Editor UI error: {exc}", text_color=DANGER)
            except Exception:
                pass
        finally:
            if not self._closing:
                self.after(40, self._drain_ui_queue)

    # ------------------------------------------------------------------ layout helpers
    def _button(self, master: tk.Misc, text: str, command: Callable[[], Any], *, width: int = 0,
                state: str = "normal", default: bool = False) -> EButton:
        kw: dict[str, Any] = {"text": text, "command": command, "state": state}
        if width:
            kw["width"] = width
        if default:
            kw["default"] = "active"
        return EButton(master, **kw)

    def _label(self, master: tk.Misc, text: str = "", *, muted: bool = False, wrap: int = 0) -> native.Text:
        label = native.Text(master, text, wrap=wrap)
        if muted:
            label.configure(foreground=MUTED)
        return label

    def _build_ui(self) -> None:
        self._build_menu()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_topbar()
        ttk.Separator(self, orient="horizontal").grid(row=1, column=0, sticky="ew")

        workspace = ttk.Frame(self, padding=(native.PAD, native.PAD, native.PAD, 0))
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(0, weight=1)

        self._build_preview(workspace)
        self._build_inspector(workspace)

        self._build_timeline()
        self._build_bottom_bar()

    def _build_menu(self) -> None:
        bar = tk.Menu(self, tearoff=0)
        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="Open source", command=self.open_source)
        file_menu.add_command(label="Choose output folder…", command=self.choose_output_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Preview edit (15s)", command=self.preview_edit_clip)
        file_menu.add_command(label="Save current frame…", command=self.save_current_frame)
        file_menu.add_command(label="Export…", command=self.export_media)
        file_menu.add_command(label="Open export", command=self.open_export)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self._close)
        bar.add_cascade(label="File", menu=file_menu, underline=0)
        edit_menu = tk.Menu(bar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo_edit)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo_edit)
        edit_menu.add_separator()
        edit_menu.add_command(label="Crop on video", accelerator="C", command=self.toggle_crop_mode)
        edit_menu.add_separator()
        edit_menu.add_command(label="Reset edits", command=self.reset_edits)
        edit_menu.add_command(label="Reset range", command=self.reset_range)
        bar.add_cascade(label="Edit", menu=edit_menu, underline=0)
        self._file_menu = file_menu
        self._edit_menu = edit_menu
        file_menu.configure(postcommand=self._refresh_menus)
        edit_menu.configure(postcommand=self._refresh_menus)
        self.configure(menu=bar)

    def _refresh_menus(self) -> None:
        def state(widget: Any) -> str:
            try:
                return "normal" if str(widget.cget("state")) != "disabled" else "disabled"
            except Exception:
                return "normal"
        self._edit_menu.entryconfigure("Undo", state=state(self.undo_button))
        self._edit_menu.entryconfigure("Redo", state=state(self.redo_button))
        self._file_menu.entryconfigure("Export…", state=state(self.export_button))
        self._file_menu.entryconfigure("Preview edit (15s)", state=state(self.preview_clip_button))
        self._file_menu.entryconfigure("Open export", state=state(self.open_export_button))

    def _build_topbar(self) -> None:
        # A plain row of push buttons under the menu, like a classic tool window.
        top = ttk.Frame(self, padding=(native.PAD, native.GAP, native.PAD, native.GAP))
        top.grid(row=0, column=0, sticky="ew")
        self.undo_button = self._button(top, "Undo", self.undo_edit, state="disabled")
        self.undo_button.pack(side="left")
        self.redo_button = self._button(top, "Redo", self.redo_edit, state="disabled")
        self.redo_button.pack(side="left", padx=(native.GAP, 0))
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=native.PAD)
        self._button(top, "Reset edits", self.reset_edits).pack(side="left")
        self._button(top, "Open source", self.open_source).pack(side="left", padx=(native.GAP, 0))
        # The file name is in the window title; these two stay for the editing code.
        self.source_label = self._label(top)
        self.loading_chip = self._label(top)

    def _build_preview(self, workspace: ttk.Frame) -> None:
        area = ttk.Frame(workspace)
        area.grid(row=0, column=0, sticky="nsew", padx=(0, native.PAD))
        area.grid_columnconfigure(0, weight=1)
        area.grid_rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            area,
            bg=PREVIEW_BG,
            bd=0,
            highlightthickness=1,
            highlightbackground=C["BORDER"],
            cursor="crosshair",
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)
        self.preview_canvas.bind("<Button-1>", self._crop_press)
        self.preview_canvas.bind("<B1-Motion>", self._crop_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self._crop_release)
        self.preview_canvas.bind("<Motion>", self._crop_hover)
        self.preview_canvas.bind("<Double-Button-1>", self._crop_double_click)
        # The embedded player draws into this frame, which sits over the canvas. Hiding it
        # uncovers the canvas, where the crop box is drawn (nothing can be drawn over mpv).
        self.video_host = tk.Frame(area, bg=PREVIEW_BG, bd=0, highlightthickness=0)
        self.preview_canvas.create_text(
            px(12),
            px(12),
            text="Loading media…",
            fill="#C0C0C0",
            anchor="nw",
            font=native.font(self),
            tags="placeholder",
        )

        transport = ttk.Frame(area)
        transport.grid(row=1, column=0, sticky="ew", pady=(native.GAP, 0))
        self.transport_bar = transport
        transport.grid_columnconfigure(5, weight=1)
        self._button(transport, "◀ 5s", lambda: self.step_playhead(-5.0), width=4).grid(row=0, column=0)
        self._button(transport, "Set In", self.set_in_here, width=6).grid(row=0, column=1, padx=(native.GAP, 0))
        self.play_button = self._button(transport, "▶ Play", self.toggle_playback, state="disabled", width=8)
        self.play_button.grid(row=0, column=2, padx=(native.GAP, 0))
        self.current_time_label = self._label(transport, "00:00.000 / --:--")
        self.current_time_label.grid(row=0, column=3, padx=native.GAP)
        self._button(transport, "Set Out", self.set_out_here, width=6).grid(row=0, column=4)
        self.preview_status_label = self._label(transport, "Space Play/Pause • I/O In/Out • ←/→ Frame", muted=True)
        self.preview_status_label.grid(row=0, column=5, sticky="w", padx=(native.GAP, native.GAP))
        self._button(transport, "5s ▶", lambda: self.step_playhead(5.0), width=4).grid(row=0, column=6)
        self.crop_video_button = self._button(transport, "✂ Crop", self.toggle_crop_mode, state="disabled", width=6)
        self.crop_video_button.grid(row=0, column=7, padx=(native.GAP, 0))

        # Shown instead of the transport row while the crop box is on screen.
        crop_bar = ttk.Frame(area)
        crop_bar.grid_columnconfigure(2, weight=1)
        self.crop_bar = crop_bar
        self._field_label(crop_bar, "Size:").grid(row=0, column=0, sticky="w")
        self.crop_bar_menu = Choice(crop_bar, self.crop_var, list(CROP_PRESETS), self._crop_changed, width=8)
        self.crop_bar_menu.grid(row=0, column=1, sticky="w", padx=(native.GAP, native.GAP))
        self.crop_size_label = self._label(crop_bar, "")
        self.crop_size_label.grid(row=0, column=2, sticky="w")
        self._button(crop_bar, "Center", self.crop_center, width=7).grid(row=0, column=3, padx=(native.GAP, 0))
        self._button(crop_bar, "Max size", self.crop_max_size, width=8).grid(row=0, column=4, padx=(native.GAP, 0))
        self._button(crop_bar, "Cancel", lambda: self.exit_crop_mode(apply=False), width=7).grid(
            row=0, column=5, padx=(native.GAP, 0))
        self._button(crop_bar, "✓ Done", lambda: self.exit_crop_mode(apply=True), width=7, default=True).grid(
            row=0, column=6, padx=(native.GAP, 0))

    def _build_inspector(self, workspace: ttk.Frame) -> None:
        self.tabs = Tabs(workspace)
        self.tabs.grid(row=0, column=1, sticky="nsew")
        self._build_video_tab(self.tabs.page("Video"))
        self._build_color_tab(self.tabs.page("Color"))
        self._build_audio_tab(self.tabs.page("Audio"))
        self._build_export_tab(self.tabs.page("Export"))

    def _field_label(self, master: Any, text: str) -> native.Text:
        return self._label(master, text)

    def _build_video_tab(self, tab: ttk.Frame) -> None:
        crop = native.group(tab, "Crop", row=0, column=0, sticky="ew")
        crop.grid_columnconfigure(1, weight=1)
        crop.grid_columnconfigure(3, weight=1)
        self._field_label(crop, "Size:").grid(row=0, column=0, sticky="w", pady=2)
        self.crop_menu = Choice(crop, self.crop_var, list(CROP_PRESETS), self._crop_changed, width=10)
        self.crop_menu.grid(row=0, column=1, sticky="ew", pady=2, padx=(native.GAP, 0))
        self.crop_edit_button = self._button(crop, "✂ Crop on video", self.toggle_crop_mode, state="disabled")
        self.crop_edit_button.grid(row=0, column=2, columnspan=2, sticky="e", pady=2, padx=(native.GAP, 0))

        self.custom_entries: list[ttk.Entry] = []
        fields = [
            ("X:", self.custom_x_var),
            ("Y:", self.custom_y_var),
            ("W:", self.custom_w_var),
            ("H:", self.custom_h_var),
        ]
        numbers = ttk.Frame(crop)
        numbers.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(2, 0))
        for index, (label, variable) in enumerate(fields):
            self._field_label(numbers, label).grid(row=0, column=index * 2, sticky="w",
                                                   padx=(0 if index == 0 else native.GAP, 2))
            entry = ttk.Entry(numbers, textvariable=variable, width=5)
            entry.grid(row=0, column=index * 2 + 1, sticky="w")
            entry.configure(state="disabled")
            entry.bind("<Return>", lambda _event: self.apply_custom_crop())
            entry.bind("<FocusOut>", lambda _event: self.apply_custom_crop(silent=True))
            self.custom_entries.append(entry)

        frame = native.group(tab, "Rotate, flip and speed", row=1, column=0, sticky="ew", pady=(native.PAD, 0))
        frame.grid_columnconfigure(4, weight=1)
        self._field_label(frame, "Rotate:").grid(row=0, column=0, sticky="w", pady=2)
        self.rotate_left_button = self._button(frame, "⟲", lambda: self.rotate_step(-1), width=3)
        self.rotate_left_button.grid(row=0, column=1, sticky="w", pady=2, padx=(native.GAP, 0))
        self.rotate_menu = Choice(frame, self.rotate_var, ROTATIONS, self._rotation_changed, width=5)
        self.rotate_menu.grid(row=0, column=2, sticky="w", pady=2, padx=(native.GAP, 0))
        self.rotate_right_button = self._button(frame, "⟳", lambda: self.rotate_step(1), width=3)
        self.rotate_right_button.grid(row=0, column=3, sticky="w", pady=2, padx=(native.GAP, 0))
        self.flip_h_check = ttk.Checkbutton(frame, text="Flip horizontal", variable=self.flip_h_var,
                                            command=self._flip_changed)
        self.flip_h_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        self.flip_v_check = ttk.Checkbutton(frame, text="Flip vertical", variable=self.flip_v_var,
                                            command=self._flip_changed)
        self.flip_v_check.grid(row=1, column=3, columnspan=2, sticky="w", pady=2, padx=(native.PAD, 0))
        self._field_label(frame, "Speed:").grid(row=2, column=0, sticky="w", pady=2)
        self.speed_menu = Choice(frame, self.speed_var, SPEEDS, self._speed_changed, width=6)
        self.speed_menu.grid(row=2, column=1, columnspan=3, sticky="w", pady=2, padx=(native.GAP, 0))
        self._button(frame, "Reset video", self.reset_video_edits).grid(row=2, column=4, sticky="e", pady=2)

    def _build_color_tab(self, tab: ttk.Frame) -> None:
        frame = native.group(tab, "Color", row=0, column=0, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self.color_sliders: list[ttk.Scale] = []
        self.color_value_labels: dict[str, native.Text] = {}
        rows = [
            ("Brightness:", "brightness", self.brightness_var),
            ("Contrast:", "contrast", self.contrast_var),
            ("Saturation:", "saturation", self.saturation_var),
        ]
        for row, (label, key, variable) in enumerate(rows):
            self._field_label(frame, label).grid(row=row, column=0, sticky="w", pady=2)

            def moved(value: str, key: str = key, variable: tk.DoubleVar = variable) -> None:
                whole = round(float(value))
                if abs(variable.get() - whole) > 1e-9:
                    variable.set(whole)
                self._color_changed(key)

            slider = ttk.Scale(frame, from_=-100, to=100, orient="horizontal", variable=variable, command=moved)
            slider.grid(row=row, column=1, sticky="ew", pady=2, padx=(native.GAP, native.GAP))
            slider.bind("<ButtonRelease-1>", lambda _event: self._record_history_snapshot())
            slider.bind("<Double-Button-1>", lambda _event, v=variable, k=key: self._reset_one_color(v, k))
            value_label = self._label(frame, "0")
            value_label.configure(width=4)
            value_label.grid(row=row, column=2, sticky="e", pady=2)
            self.color_sliders.append(slider)
            self.color_value_labels[key] = value_label

        self._label(tab, "Double-click a slider to reset it. The video preview shows the change live.",
                    muted=True, wrap=px(280)).grid(row=1, column=0, sticky="ew", pady=(native.PAD, 0))
        self._button(tab, "Reset color", self.reset_color, width=16).grid(
            row=2, column=0, sticky="e", pady=(native.PAD, 0))

    def _build_audio_tab(self, tab: ttk.Frame) -> None:
        frame = native.group(tab, "Audio", row=0, column=0, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self._field_label(frame, "Speed:").grid(row=0, column=0, sticky="w", pady=2)
        self.audio_speed_menu = Choice(frame, self.speed_var, SPEEDS, self._speed_changed, width=6)
        self.audio_speed_menu.grid(row=0, column=1, sticky="w", pady=2, padx=(native.GAP, 0))
        self.mute_switch = ttk.Checkbutton(frame, text="Mute audio", variable=self.mute_var,
                                           command=self._sync_audio_state)
        self.mute_switch.grid(row=1, column=0, columnspan=3, sticky="w", pady=(native.GAP, 2))

        self._field_label(frame, "Volume:").grid(row=2, column=0, sticky="w", pady=2)

        def volume_moved(value: str) -> None:
            whole = round(float(value))  # whole percent steps, as before
            if abs(self.volume_var.get() - whole) > 1e-9:
                self.volume_var.set(whole)
            self._volume_changed(whole)

        self.volume_slider = ttk.Scale(frame, from_=0, to=200, orient="horizontal", variable=self.volume_var,
                                       command=volume_moved)
        self.volume_slider.grid(row=2, column=1, sticky="ew", pady=2, padx=(native.GAP, native.GAP))
        self.volume_slider.bind("<ButtonRelease-1>", lambda _event: self._record_history_snapshot())
        self.volume_text = self._label(frame, "Volume 100%")
        self.volume_text.grid(row=2, column=2, sticky="w", pady=2)

        fade = native.group(tab, "Fade", row=1, column=0, sticky="ew", pady=(native.PAD, 0))
        self._field_label(fade, "Fade in:").grid(row=0, column=0, sticky="w", pady=2)
        self.fade_in_entry = ttk.Entry(fade, textvariable=self.fade_in_var, width=7)
        self.fade_in_entry.grid(row=0, column=1, sticky="w", pady=2, padx=(native.GAP, native.GAP))
        self._field_label(fade, "sec").grid(row=0, column=2, sticky="w")
        self._field_label(fade, "Fade out:").grid(row=0, column=3, sticky="w", pady=2, padx=(native.PAD, 0))
        self.fade_out_entry = ttk.Entry(fade, textvariable=self.fade_out_var, width=7)
        self.fade_out_entry.grid(row=0, column=4, sticky="w", pady=2, padx=(native.GAP, native.GAP))
        self._field_label(fade, "sec").grid(row=0, column=5, sticky="w")
        for entry in (self.fade_in_entry, self.fade_out_entry):
            entry.bind("<Return>", lambda _event: self._record_history_snapshot())
            entry.bind("<FocusOut>", lambda _event: self._record_history_snapshot())

        self._button(tab, "Reset audio edits", self.reset_audio_edits, width=16).grid(
            row=2, column=0, sticky="e", pady=(native.PAD, 0))

    def _build_export_tab(self, tab: ttk.Frame) -> None:
        frame = native.group(tab, "Output", row=0, column=0, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self._field_label(frame, "File name:").grid(row=0, column=0, sticky="w", pady=2)
        self.output_name_entry = ttk.Entry(frame, textvariable=self.output_name_var)
        self.output_name_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=2, padx=(native.GAP, 0))
        self._field_label(frame, "Format:").grid(row=1, column=0, sticky="w", pady=2)
        self.format_menu = Choice(frame, self.format_var, ["MP4", "MKV", "MOV"],
                                  lambda _value: self._update_export_summary(), width=7)
        self.format_menu.grid(row=1, column=1, sticky="w", pady=2, padx=(native.GAP, 0))
        self._field_label(frame, "Quality:").grid(row=1, column=2, sticky="e", pady=2, padx=(native.PAD, 0))
        self.quality_menu = Choice(frame, self.quality_var, ["High", "Balanced", "Small"],
                                   lambda _value: self._update_export_summary(), width=9)
        self.quality_menu.grid(row=1, column=3, sticky="w", pady=2, padx=(native.GAP, 0))
        self._field_label(frame, "Size:").grid(row=2, column=0, sticky="w", pady=2)
        self.resolution_menu = Choice(frame, self.resolution_var, list(RESOLUTIONS), self._resolution_changed,
                                      width=9)
        self.resolution_menu.grid(row=2, column=1, sticky="w", pady=2, padx=(native.GAP, 0))
        self._field_label(frame, "Folder:").grid(row=3, column=0, sticky="nw", pady=2)
        self.output_dir_label = self._label(frame, _short_path(self.output_dir), wrap=px(240))
        self.output_dir_label.grid(row=3, column=1, columnspan=2, sticky="ew", pady=2, padx=(native.GAP, 0))
        self.choose_output_button = self._button(frame, "Browse…", self.choose_output_folder)
        self.choose_output_button.grid(row=3, column=3, sticky="ne", pady=2)

        summary = native.group(tab, "Output summary", row=1, column=0, sticky="ew", pady=(native.PAD, 0))
        summary.grid_columnconfigure(0, weight=1)
        self.export_summary_label = self._label(summary, "Load media to calculate output details.", wrap=px(280))
        self.export_summary_label.grid(row=0, column=0, sticky="ew")

        self.save_frame_button = self._button(tab, "Save current frame…", self.save_current_frame, state="disabled")
        self.save_frame_button.grid(row=2, column=0, sticky="w", pady=(native.PAD, 0))
        self._label(tab, "Your source file is never overwritten.", muted=True, wrap=px(300)).grid(
            row=3, column=0, sticky="ew", pady=(native.PAD, 0))

    def _build_timeline(self) -> None:
        card = native.group(self, "Timeline", row=3, column=0, sticky="ew", padx=native.PAD, pady=(native.PAD, 0))
        card.grid_columnconfigure(0, weight=1)

        header = ttk.Frame(card)
        header.grid(row=0, column=0, sticky="ew", pady=(0, native.GAP))
        header.grid_columnconfigure(1, weight=1)
        self.selection_label = self._label(header, "Selection: --")
        self.selection_label.grid(row=0, column=0, sticky="w")
        self._button(header, "Fit", self.timeline_fit, width=5).grid(row=0, column=2, padx=(0, native.GAP))
        self._button(header, "−", self.timeline_zoom_out, width=3).grid(row=0, column=3, padx=(0, native.GAP))
        self._button(header, "+", self.timeline_zoom_in, width=3).grid(row=0, column=4)

        self.timeline = TimelineCanvas(
            card,
            on_seek=self._on_timeline_seek,
            on_range_change=self._on_range_change,
        )
        self.timeline.grid(row=1, column=0, sticky="ew")

        range_row = ttk.Frame(card)
        range_row.grid(row=2, column=0, sticky="ew", pady=(native.GAP, 0))
        self._field_label(range_row, "In:").grid(row=0, column=0, padx=(0, native.GAP))
        self.start_entry = ttk.Entry(range_row, textvariable=self.start_var, width=12)
        self.start_entry.grid(row=0, column=1, sticky="w")
        self.start_entry.bind("<Return>", lambda _event: self.apply_range_entries())
        self.start_entry.bind("<FocusOut>", lambda _event: self.apply_range_entries(silent=True))
        self._field_label(range_row, "Out:").grid(row=0, column=2, padx=(native.PAD, native.GAP))
        self.end_entry = ttk.Entry(range_row, textvariable=self.end_var, width=12)
        self.end_entry.grid(row=0, column=3, sticky="w")
        self.end_entry.bind("<Return>", lambda _event: self.apply_range_entries())
        self.end_entry.bind("<FocusOut>", lambda _event: self.apply_range_entries(silent=True))
        self._button(range_row, "Reset range", self.reset_range).grid(row=0, column=4, padx=(native.PAD, 0))

    def timeline_fit(self) -> None:
        if not self.render_busy:
            self.timeline.fit_view()

    def timeline_zoom_in(self) -> None:
        if not self.render_busy:
            self.timeline.zoom_in()

    def timeline_zoom_out(self) -> None:
        if not self.render_busy:
            self.timeline.zoom_out()

    def _build_bottom_bar(self) -> None:
        actions = ttk.Frame(self, padding=(native.PAD, native.PAD, native.PAD, native.PAD))
        actions.grid(row=4, column=0, sticky="ew")
        self.preview_clip_button = self._button(actions, "Preview edit (15s)", self.preview_edit_clip, width=17,
                                                state="disabled")
        self.preview_clip_button.pack(side="left")
        self.cancel_button = self._button(actions, "Cancel", self.cancel_export, state="disabled")
        self.cancel_button.pack(side="right", padx=(native.GAP, 0))
        self.export_button = self._button(actions, "Export…", self.export_media, state="disabled", default=True)
        self.export_button.pack(side="right", padx=(native.GAP, 0))
        self.open_export_button = self._button(actions, "Open export", self.open_export, state="disabled")
        self.open_export_button.pack(side="right")

        # Windows status bar: message, progress bar, media details.
        ttk.Separator(self, orient="horizontal").grid(row=5, column=0, sticky="ew")
        bar = ttk.Frame(self, padding=(native.GAP, 2, native.GAP, 2))
        bar.grid(row=6, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        self.status_label = self._label(bar, "Loading media…")
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.progress = Progress(bar, length=px(160))
        self.progress.grid(row=0, column=1, padx=native.PAD)
        self.progress.set(0)
        ttk.Separator(bar, orient="vertical").grid(row=0, column=2, sticky="ns")
        self.status_info = self._label(bar, "")
        self.status_info.grid(row=0, column=3, sticky="e", padx=(native.GAP, 0))
        self.media_info_label = self.status_info  # the editing code writes media details here

    def _start_load(self) -> None:
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        try:
            if not self.source_path.exists():
                raise FileNotFoundError("The source file no longer exists.")
            info = probe_media(self.source_path)
            self._post_ui(self._apply_loaded_media, info)
        except Exception as exc:
            self._post_ui(self._load_failed, str(exc))

    def _apply_loaded_media(self, info: MediaInfo) -> None:
        self.info = info
        self.start_var.set("00:00.000")
        self.end_var.set(format_time(info.duration))
        self.playhead_var.set(min(info.duration, 1.0))
        self.custom_w_var.set(str(info.width or ""))
        self.custom_h_var.set(str(info.height or ""))

        kind = "Video" if info.has_video else "Audio"
        meta = f"{kind} • {format_time(info.duration)}"
        if info.has_video and info.width and info.height:
            meta += f" • {info.width}×{info.height}"
            if info.fps:
                meta += f" • {info.fps:g} fps"
        self.source_label.configure(text=f"{self.source_path.name}  •  {meta}")
        self.media_info_label.configure(text=meta)

        self.timeline.set_media(info.duration, 0.0, info.duration, self.playhead_var.get())
        self._update_range_labels()
        self._update_current_time_label()
        self._init_embedded_player()

        if info.has_video:
            self.format_menu.configure(values=["MP4", "MKV", "MOV"])
            source_format = self.source_path.suffix.upper().lstrip(".")
            self.format_var.set(source_format if source_format in {"MP4", "MKV", "MOV"} else "MP4")
            self.tabs.set("Video")
            self.crop_video_button.configure(state="normal")
            self.crop_edit_button.configure(state="normal")
            self.save_frame_button.configure(state="normal")
            self.schedule_preview(delay=10)
            self._start_thumbnail_generation()
        else:
            self.format_menu.configure(values=["MP3", "M4A", "WAV"])
            source_format = self.source_path.suffix.upper().lstrip(".")
            self.format_var.set(source_format if source_format in {"MP3", "M4A", "WAV"} else "MP3")
            self.tabs.set("Audio")
            for widget in self._video_only_controls():
                widget.configure(state="disabled")
            self.speed_menu.configure(state="normal")
            self.resolution_menu.configure(state="disabled")
            self._start_waveform_generation()

        if not info.has_audio:
            self.mute_switch.configure(state="disabled")
            self.volume_slider.configure(state="disabled")
            self.fade_in_entry.configure(state="disabled")
            self.fade_out_entry.configure(state="disabled")

        self.loading_chip.configure(text="READY", fg_color="#0D2A2A", text_color=SUCCESS)
        self.status_label.configure(text="Ready to edit", text_color=SUCCESS)
        self.export_button.configure(state="normal")
        self.preview_clip_button.configure(state="normal")
        if not info.has_video:
            self.quality_menu.configure(state="disabled")
        self._update_export_summary()
        self.history.clear()
        self.history.push(self._capture_history_snapshot())
        self._update_history_buttons()

    def _load_failed(self, error: str) -> None:
        LOGGER.error("Media load failed source=%s error=%s", self.source_path, error)
        self.loading_chip.configure(text="ERROR", fg_color="#31131B", text_color=DANGER)
        self.status_label.configure(text="Could not load media", text_color=DANGER)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            max(20, self.preview_canvas.winfo_width() / 2),
            max(20, self.preview_canvas.winfo_height() / 2),
            text="Media could not be loaded",
            fill=DANGER,
            font=("Segoe UI Semibold", 14),
        )
        messagebox.showerror(APP_TITLE, error, parent=self)

    def _on_preview_resize(self, _event=None) -> None:
        if self.crop_mode:
            self._draw_crop_overlay()
            return
        if self.player is not None and self.player.ready:
            return
        if self.preview_pil is not None:
            self._draw_preview_image(self.preview_pil)

    def _draw_preview_image(self, image: Image.Image) -> None:
        width = max(100, self.preview_canvas.winfo_width() - 18)
        height = max(100, self.preview_canvas.winfo_height() - 18)
        display = image.copy()
        display.thumbnail((width, height))
        self.preview_photo = ImageTk.PhotoImage(display)

        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            self.preview_canvas.winfo_width() / 2,
            self.preview_canvas.winfo_height() / 2,
            image=self.preview_photo,
            anchor="center",
        )

    def _transform_live_frame(self, image: Image.Image) -> Image.Image:
        if not self.info or not self.info.has_video:
            return image
        try:
            crop = compute_crop(self.info, self.crop_var.get(), self._current_custom_crop())
            if crop and self.info.width > 0 and self.info.height > 0:
                x, y, width, height = crop
                scale_x = image.width / self.info.width
                scale_y = image.height / self.info.height
                left = max(0, int(round(x * scale_x)))
                top = max(0, int(round(y * scale_y)))
                right = min(image.width, int(round((x + width) * scale_x)))
                bottom = min(image.height, int(round((y + height) * scale_y)))
                if right > left and bottom > top:
                    image = image.crop((left, top, right, bottom))
        except Exception:
            pass

        rotate = self.rotate_var.get()
        if rotate == "90°":
            image = image.transpose(Image.Transpose.ROTATE_270)
        elif rotate == "180°":
            image = image.transpose(Image.Transpose.ROTATE_180)
        elif rotate == "270°":
            image = image.transpose(Image.Transpose.ROTATE_90)
        return image

    def _init_embedded_player(self) -> None:
        try:
            window_id = None
            if self.info and self.info.has_video:
                if not self.crop_mode:
                    self._show_video_host(True)
                self.update_idletasks()
                window_id = self.video_host.winfo_id()
            player = EmbeddedMediaPlayer(
                self.source_path,
                window_id=window_id,
                audio_only=bool(self.info and not self.info.has_video),
            )
            self.player = player
            self.play_button.configure(text="Starting…", state="disabled")
            self.preview_status_label.configure(text="Starting crash-isolated playback engine…", text_color=WARNING)
            threading.Thread(
                target=self._wait_for_player_worker,
                args=(player,),
                daemon=True,
            ).start()
        except Exception as exc:
            self._player_failed(None, str(exc))

    def _wait_for_player_worker(self, player: EmbeddedMediaPlayer) -> None:
        ready = False
        try:
            ready = player.wait_until_ready(timeout=6.0)
            error = player.last_error
        except Exception as exc:
            error = str(exc)
        self._post_ui(self._finish_player_startup, player, ready, error)

    def _finish_player_startup(
        self,
        player: EmbeddedMediaPlayer,
        ready: bool,
        error: str,
    ) -> None:
        if self._closing or self.player is not player:
            try:
                player.close()
            except Exception:
                pass
            return
        if not ready:
            self._player_failed(player, error or "Playback worker did not become ready.")
            return
        try:
            player.set_volume(self.volume_var.get() / 100.0)
            player.set_mute(self.mute_var.get())
            self.player_rate_supported = player.set_rate(self._speed_value())
            self._apply_player_video_transform()
            player.seek(float(self.playhead_var.get()))
            self.preview_generation += 1
            if self.info and self.info.has_video and not self.crop_mode:
                self.preview_canvas.delete("all")
            self.play_button.configure(text="▶ Play", state="normal")
            self.preview_status_label.configure(text="Embedded mpv player ready • Space = Play/Pause", text_color=MUTED)
            self._schedule_player_tick(20)
        except Exception as exc:
            self._player_failed(player, str(exc))

    def _player_failed(
        self,
        player: EmbeddedMediaPlayer | None,
        error: str,
    ) -> None:
        if player is not None and self.player is not player:
            return
        old_player = self.player
        self.player = None
        self.playing = False
        if old_player is not None:
            try:
                old_player.close()
            except Exception:
                pass
        self._show_video_host(False)
        self.play_button.configure(text="No player", state="disabled")
        self.preview_status_label.configure(
            text="Native player stopped safely • using frame preview",
            text_color=WARNING,
        )
        detail = (error or "Playback helper stopped unexpectedly.").strip()
        LOGGER.warning("Playback fallback active source=%s error=%s", self.source_path, detail)
        self.status_label.configure(text=f"Playback fallback active: {detail[-240:]}", text_color=WARNING)
        if self.info and self.info.has_video and not self.crop_mode:
            self.schedule_preview(delay=20)

    def _show_video_host(self, visible: bool) -> None:
        if visible:
            self.video_host.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
            self.video_host.lift(self.preview_canvas)
        else:
            self.video_host.grid_remove()

    def _schedule_player_tick(self, delay: int = 18) -> None:
        if self._closing or self.player is None:
            return
        if self.player_tick_id is not None:
            try:
                self.after_cancel(self.player_tick_id)
            except Exception:
                pass
        self.player_tick_id = self.after(max(5, delay), self._player_tick)

    def _player_tick(self) -> None:
        self.player_tick_id = None
        if self._closing or self.player is None:
            return
        try:
            position = self.player.position()
            if position is not None:
                position = max(0.0, min(float(position), self.info.duration if self.info else float(position)))
                if self.playing and self.info:
                    try:
                        selection_end = parse_time(self.end_var.get())
                    except Exception:
                        selection_end = self.info.duration
                    if position >= selection_end - 0.02:
                        self.pause_playback()
                        position = selection_end
                self.playhead_var.set(position)
                self.timeline.set_playhead(position)
                self._update_current_time_label()

            self._schedule_player_tick(24 if self.playing else 100)
        except Exception as exc:
            self.pause_playback()
            failed_player = self.player
            self._player_failed(failed_player, str(exc))

    def _set_render_busy(self, busy: bool) -> None:
        self.render_busy = busy
        state = "disabled" if busy else "normal"
        controls = [
            *self._video_only_controls(),
            self.speed_menu,
            self.audio_speed_menu,
            self.mute_switch,
            self.volume_slider,
            self.fade_in_entry,
            self.fade_out_entry,
            self.output_name_entry,
            self.format_menu,
            self.quality_menu,
            self.choose_output_button,
            self.start_entry,
            self.end_entry,
            self.resolution_menu,
        ]
        for widget in controls:
            try:
                widget.configure(state=state)
            except Exception:
                pass
        try:
            self.timeline.configure(state="disabled" if busy else "normal")
        except Exception:
            pass

        if busy:
            self.pause_playback()
            self.play_button.configure(state="disabled")
        else:
            if self.player is not None and self.player.ready:
                self.play_button.configure(state="normal")
            self._sync_crop_entries()
            self._sync_audio_state()
            if self.info is not None and not self.info.has_video:
                for widget in self._video_only_controls():
                    widget.configure(state="disabled")
                self.quality_menu.configure(state="disabled")
                self.resolution_menu.configure(state="disabled")
            if self.info is not None and not self.info.has_audio:
                self.mute_switch.configure(state="disabled")
                self.volume_slider.configure(state="disabled")
                self.fade_in_entry.configure(state="disabled")
                self.fade_out_entry.configure(state="disabled")

    def toggle_playback(self) -> None:
        if self.render_busy:
            return
        if self.crop_mode:
            self.exit_crop_mode(apply=True)
        if self.player is None:
            messagebox.showinfo(
                APP_TITLE,
                "Embedded playback is unavailable in this build. Frame scrubbing and rendered preview are still available.",
                parent=self,
            )
            return
        if self.playing:
            self.pause_playback()
        else:
            self.start_playback()

    def start_playback(self) -> None:
        if self.render_busy:
            return
        if self.player is None or not self.info:
            return
        if not self.player.ready:
            self.status_label.configure(text="Playback engine is still starting…", text_color=WARNING)
            return
        try:
            start = parse_time(self.start_var.get())
            end = parse_time(self.end_var.get())
            position = float(self.playhead_var.get())
            if position < start or position >= end - 0.02:
                position = start
                self.player.seek(position)
                self.playhead_var.set(position)
                self.timeline.set_playhead(position)
            self.player.set_volume(self.volume_var.get() / 100.0)
            self.player.set_mute(self.mute_var.get())
            self.player_rate_supported = self.player.set_rate(self._speed_value())
            self.player.play()
            self.playing = True
            self.play_button.configure(text="Ⅱ Pause", fg_color=CYAN, hover_color=CYAN_HOVER, text_color="#031018")
            rate_note = "" if self.player_rate_supported else " • speed applies on export"
            self.preview_status_label.configure(text=f"Playing selected range{rate_note}", text_color=SUCCESS)
            self._schedule_player_tick(5)
        except Exception as exc:
            self.playing = False
            failed_player = self.player
            self._player_failed(failed_player, str(exc))

    def pause_playback(self) -> None:
        if self.player is not None:
            try:
                self.player.pause()
            except Exception:
                pass
        self.playing = False
        if hasattr(self, "play_button"):
            self.play_button.configure(text="▶ Play", fg_color=PURPLE, hover_color=PURPLE_HOVER, text_color=TEXT)
        if hasattr(self, "preview_status_label"):
            self.preview_status_label.configure(text="Paused • Space = Play/Pause", text_color=MUTED)

    def _seek_player(self, value: float) -> None:
        if self.render_busy:
            return
        if not self.info:
            return
        value = max(0.0, min(self.info.duration, float(value)))
        self.playhead_var.set(value)
        self.timeline.set_playhead(value)
        self._update_current_time_label()
        if self.crop_mode:
            if self.player is not None:
                try:
                    self.player.seek(value)
                except Exception:
                    pass
            self._request_crop_frame(delay=120)
            return
        if self.player is not None:
            try:
                self.player.seek(value)
                self._schedule_player_tick(5)
                return
            except Exception:
                pass
        self.schedule_preview(delay=40)

    def _shortcut_allowed(self) -> bool:
        focus = self.focus_get()
        try:
            if focus is not None and focus.winfo_class() in {"Entry", "TEntry", "Text"}:
                return False
        except Exception:
            pass
        return True

    def _capture_history_snapshot(self) -> EditorSnapshot:
        return EditorSnapshot(
            start=self.start_var.get(),
            end=self.end_var.get(),
            crop=self.crop_var.get(),
            rotate=self.rotate_var.get(),
            speed=self.speed_var.get(),
            custom_x=self.custom_x_var.get(),
            custom_y=self.custom_y_var.get(),
            custom_w=self.custom_w_var.get(),
            custom_h=self.custom_h_var.get(),
            mute=bool(self.mute_var.get()),
            volume=float(self.volume_var.get()),
            fade_in=self.fade_in_var.get(),
            fade_out=self.fade_out_var.get(),
            flip_h=bool(self.flip_h_var.get()),
            flip_v=bool(self.flip_v_var.get()),
            brightness=float(self.brightness_var.get()),
            contrast=float(self.contrast_var.get()),
            saturation=float(self.saturation_var.get()),
            resolution=self.resolution_var.get(),
        )

    def _record_history_snapshot(self) -> None:
        if self._applying_history or self.info is None or self.render_busy:
            return
        if self.history.push(self._capture_history_snapshot()):
            self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        if not hasattr(self, "undo_button"):
            return
        self.undo_button.configure(state="normal" if self.history.can_undo else "disabled")
        self.redo_button.configure(state="normal" if self.history.can_redo else "disabled")

    def _apply_history_snapshot(self, snapshot: EditorSnapshot) -> None:
        if self.info is None:
            return
        self._applying_history = True
        try:
            self.start_var.set(snapshot.start)
            self.end_var.set(snapshot.end)
            self.crop_var.set(snapshot.crop)
            self.rotate_var.set(snapshot.rotate)
            self.speed_var.set(snapshot.speed)
            self.custom_x_var.set(snapshot.custom_x)
            self.custom_y_var.set(snapshot.custom_y)
            self.custom_w_var.set(snapshot.custom_w)
            self.custom_h_var.set(snapshot.custom_h)
            self.mute_var.set(snapshot.mute)
            self.volume_var.set(snapshot.volume)
            self.fade_in_var.set(snapshot.fade_in)
            self.fade_out_var.set(snapshot.fade_out)
            self.flip_h_var.set(snapshot.flip_h)
            self.flip_v_var.set(snapshot.flip_v)
            self.brightness_var.set(snapshot.brightness)
            self.contrast_var.set(snapshot.contrast)
            self.saturation_var.set(snapshot.saturation)
            self.resolution_var.set(snapshot.resolution)
            self._update_color_labels()

            start = max(0.0, min(parse_time(snapshot.start), self.info.duration))
            end = max(start + 0.001, min(parse_time(snapshot.end), self.info.duration))
            self.timeline.set_range(start, end)
            if self.playhead_var.get() < start or self.playhead_var.get() > end:
                self._seek_player(start)

            self._sync_crop_entries()
            if self.crop_mode:
                self.crop_rect = self._crop_rect_from_vars()
                self._draw_crop_overlay()

            self._speed_changed(snapshot.speed)
            self._volume_changed(snapshot.volume)
            self._sync_audio_state()
            self._apply_player_video_transform()
            self.schedule_preview(delay=20)
            self._update_range_labels()
            self._update_export_summary()
        finally:
            self._applying_history = False
        self._update_history_buttons()

    def undo_edit(self) -> None:
        if self.render_busy or self.crop_drag is not None:
            return
        snapshot = self.history.undo()
        if snapshot is not None:
            self._apply_history_snapshot(snapshot)
            self.status_label.configure(text="Undo applied", text_color=MUTED)

    def redo_edit(self) -> None:
        if self.render_busy or self.crop_drag is not None:
            return
        snapshot = self.history.redo()
        if snapshot is not None:
            self._apply_history_snapshot(snapshot)
            self.status_label.configure(text="Redo applied", text_color=MUTED)

    def _on_history_shortcut(self, _event: Any, action: str) -> str | None:
        if not self._shortcut_allowed() or self.render_busy:
            return None
        if action == "undo":
            self.undo_edit()
        else:
            self.redo_edit()
        return "break"

    def _on_mark_shortcut(self, _event: Any, target: str) -> str | None:
        if not self._shortcut_allowed() or self.render_busy:
            return None
        if target == "in":
            self.set_in_here()
        else:
            self.set_out_here()
        return "break"

    def _on_arrow_shortcut(self, _event: Any, direction: int, coarse: bool) -> str | None:
        if not self._shortcut_allowed() or self.render_busy:
            return None
        if self.crop_mode:
            self.crop_nudge(direction * (10 if coarse else 1), 0)
            return "break"
        if coarse:
            delta = 5.0 * direction
        else:
            fps = self.info.fps if self.info and self.info.fps > 0 else 25.0
            delta = direction / max(1.0, fps)
        self.step_playhead(delta)
        return "break"

    def _on_space(self, _event: Any) -> str | None:
        focus = self.focus_get()
        try:
            if focus is not None and focus.winfo_class() in {"Entry", "TEntry", "Text"}:
                return None
        except Exception:
            pass
        self.toggle_playback()
        return "break"

    def schedule_preview(self, delay: int = 140) -> None:
        if not self.info or not self.info.has_video or self.crop_mode:
            return
        if self.player is not None and self.player.ready:
            return
        if self.preview_after_id is not None:
            try:
                self.after_cancel(self.preview_after_id)
            except Exception:
                pass
        self.preview_after_id = self.after(delay, self.refresh_preview)

    def refresh_preview(self) -> None:
        if not self.info or not self.info.has_video:
            return
        self.preview_after_id = None
        self.preview_generation += 1
        generation = self.preview_generation
        position = float(self.playhead_var.get())
        try:
            filters = build_video_filters(
                self.info,
                self.crop_var.get(),
                self._current_custom_crop(),
                self.rotate_var.get(),
                self._speed_value(),
                include_speed=False,
                preview_size=(960, 540),
                **self._video_adjustments(),
            )
        except Exception as exc:
            self._preview_failed(str(exc))
            return

        self.preview_status_label.configure(text="Rendering preview…", text_color=WARNING)
        threading.Thread(
            target=self._preview_worker,
            args=(generation, position, filters),
            daemon=True,
        ).start()

    def _preview_worker(self, generation: int, position: float, filters: list[str]) -> None:
        try:
            with self.preview_lock:
                if generation != self.preview_generation:
                    return
                data = extract_preview_frame(self.source_path, position, filters, timeout=10)
            if generation != self.preview_generation:
                return
            image = Image.open(io.BytesIO(data)).convert("RGB")
            self._post_ui(self._apply_preview_frame, generation, image)
        except Exception as exc:
            if generation == self.preview_generation:
                self._post_ui(self._preview_failed, str(exc))

    def _apply_preview_frame(self, generation: int, image: Image.Image) -> None:
        if generation != self.preview_generation or self.crop_mode:
            return
        if self.player is not None and self.player.ready:
            return
        self.preview_pil = image
        self._draw_preview_image(image)
        self.preview_status_label.configure(text="Preview ready", text_color=MUTED)

    def _preview_failed(self, error: str) -> None:
        LOGGER.warning("Frame preview failed source=%s error=%s", self.source_path, error)
        self.preview_status_label.configure(text="Preview failed", text_color=DANGER)
        self.status_label.configure(text=f"Preview error: {error[-180:]}", text_color=DANGER)

    def _start_thumbnail_generation(self) -> None:
        if not self.info or not self.info.has_video:
            return
        self.thumbnail_generation += 1
        generation = self.thumbnail_generation
        info = self.info
        threading.Thread(
            target=self._thumbnail_worker,
            args=(generation, info),
            daemon=True,
        ).start()

    def _thumbnail_worker(self, generation: int, info: MediaInfo) -> None:
        images: list[Image.Image] = []
        times: list[float] = []
        count = 12
        for index in range(count):
            if generation != self.thumbnail_generation:
                return
            position = 0.0 if count == 1 else info.duration * index / (count - 1)
            try:
                filters = [
                    "scale=150:84:force_original_aspect_ratio=decrease",
                    "pad=150:84:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
                data = extract_preview_frame(self.source_path, position, filters, timeout=7)
                images.append(Image.open(io.BytesIO(data)).convert("RGB"))
                times.append(position)
            except Exception:
                continue
        if generation == self.thumbnail_generation and images:
            self._post_ui(self.timeline.set_thumbnails, images, times)

    def _start_waveform_generation(self) -> None:
        threading.Thread(target=self._waveform_worker, daemon=True).start()

    def _waveform_worker(self) -> None:
        try:
            data = extract_waveform(self.source_path)
            image = Image.open(io.BytesIO(data)).convert("RGB")
            self._post_ui(self._apply_waveform, image)
        except Exception as exc:
            self._post_ui(self._preview_failed, str(exc))

    def _apply_waveform(self, image: Image.Image) -> None:
        self.preview_pil = image
        self._draw_preview_image(image)
        self.timeline.set_waveform(image)
        self.preview_status_label.configure(text="Audio waveform", text_color=MUTED)

    def _on_timeline_seek(self, value: float) -> None:
        self._seek_player(value)

    def _on_range_change(self, start: float, end: float) -> None:
        self.start_var.set(format_time(start))
        self.end_var.set(format_time(end))
        self._update_range_labels()
        self._update_export_summary()
        self._seek_player(self.timeline.playhead)
        self._record_history_snapshot()

    def _update_current_time_label(self) -> None:
        if not self.info:
            self.current_time_label.configure(text="00:00.000 / --:--")
            return
        self.current_time_label.configure(
            text=f"{format_time(self.playhead_var.get())} / {format_time(self.info.duration)}"
        )

    def _update_range_labels(self) -> None:
        if not self.info:
            self.selection_label.configure(text="Selection: --")
            return
        try:
            start = parse_time(self.start_var.get())
            end = parse_time(self.end_var.get())
            duration = max(0.0, end - start)
            self.selection_label.configure(
                text=f"Selection: {format_time(duration)}  •  {format_time(start)} → {format_time(end)}"
            )
        except Exception:
            self.selection_label.configure(text="Selection: invalid range")

    def _update_export_summary(self) -> None:
        if not hasattr(self, "export_summary_label") or self.info is None:
            return
        try:
            start = max(0.0, min(parse_time(self.start_var.get()), self.info.duration))
            end = max(start, min(parse_time(self.end_var.get()), self.info.duration))
            selected = max(0.0, end - start)
            speed = max(0.01, self._speed_value())
            output_duration = selected / speed
            fmt = self.format_var.get()
            if self.info.has_video:
                crop = compute_crop(self.info, self.crop_var.get(), self._current_custom_crop())
                width, height = output_dimensions(self.info, crop, self.rotate_var.get(), self.resolution_var.get())
                dimensions = f"{width}×{height}" if width and height else "source resolution"
                detail = (
                    f"{fmt} • {self.quality_var.get()} • {dimensions}\n"
                    f"Selection {format_time(selected)} → output ≈ {format_time(output_duration)} at {speed:g}×"
                )
            else:
                codec_note = "PCM lossless" if fmt == "WAV" else "192 kbps audio"
                detail = (
                    f"{fmt} • {codec_note}\n"
                    f"Selection {format_time(selected)} → output ≈ {format_time(output_duration)} at {speed:g}×"
                )
            self.export_summary_label.configure(text=detail)
        except Exception:
            self.export_summary_label.configure(text="Adjust the edit settings to calculate output details.")

    def apply_range_entries(self, silent: bool = False) -> None:
        if not self.info:
            return
        try:
            start = parse_time(self.start_var.get())
            end = parse_time(self.end_var.get())
            start = max(0.0, min(start, self.info.duration))
            end = max(0.0, min(end, self.info.duration))
            if end <= start:
                raise ValueError("OUT must be after IN.")
            self.start_var.set(format_time(start))
            self.end_var.set(format_time(end))
            self.timeline.set_range(start, end)
            self.playhead_var.set(self.timeline.playhead)
            self._update_range_labels()
            self._update_current_time_label()
            self._update_export_summary()
            self.schedule_preview()
            self._record_history_snapshot()
        except Exception as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def set_in_here(self) -> None:
        if not self.info:
            return
        playhead = float(self.playhead_var.get())
        end = parse_time(self.end_var.get())
        if playhead >= end:
            messagebox.showwarning(APP_TITLE, "IN must be before OUT.", parent=self)
            return
        self.start_var.set(format_time(playhead))
        self.timeline.set_range(playhead, end)
        self._update_range_labels()
        self._update_export_summary()
        self._record_history_snapshot()

    def set_out_here(self) -> None:
        if not self.info:
            return
        playhead = float(self.playhead_var.get())
        start = parse_time(self.start_var.get())
        if playhead <= start:
            messagebox.showwarning(APP_TITLE, "OUT must be after IN.", parent=self)
            return
        self.end_var.set(format_time(playhead))
        self.timeline.set_range(start, playhead)
        self._update_range_labels()
        self._update_export_summary()
        self._record_history_snapshot()

    def step_playhead(self, delta: float) -> None:
        if not self.info:
            return
        self._seek_player(self.playhead_var.get() + delta)

    def reset_range(self) -> None:
        if not self.info:
            return
        previous = self._applying_history
        self._applying_history = True
        try:
            self.pause_playback()
            self.start_var.set("00:00.000")
            self.end_var.set(format_time(self.info.duration))
            self.timeline.set_media(self.info.duration, 0.0, self.info.duration, 0.0)
            self._update_range_labels()
            self._update_export_summary()
            self._seek_player(0.0)
        finally:
            self._applying_history = previous
        if not previous:
            self._record_history_snapshot()

    # ------------------------------------------------------------------ crop, rotate, flip, colour
    def _video_only_controls(self) -> list[Any]:
        return [
            self.crop_menu,
            self.crop_bar_menu,
            self.crop_edit_button,
            self.crop_video_button,
            *self.custom_entries,
            self.rotate_menu,
            self.rotate_left_button,
            self.rotate_right_button,
            self.flip_h_check,
            self.flip_v_check,
            *self.color_sliders,
            self.save_frame_button,
        ]

    def _sync_crop_entries(self) -> None:
        has_video = bool(self.info and self.info.has_video)
        active = has_video and self.crop_var.get() != "Original" and not self.render_busy
        for entry in self.custom_entries:
            entry.configure(state="normal" if active else "disabled")
        crop_state = "normal" if has_video and not self.render_busy else "disabled"
        self.crop_edit_button.configure(state=crop_state,
                                        text="✓ Done cropping" if self.crop_mode else "✂ Crop on video")
        self.crop_video_button.configure(state=crop_state)

    def _video_adjustments(self) -> dict[str, Any]:
        return {
            "flip_h": bool(self.flip_h_var.get()),
            "flip_v": bool(self.flip_v_var.get()),
            "brightness": float(self.brightness_var.get()),
            "contrast": float(self.contrast_var.get()),
            "saturation": float(self.saturation_var.get()),
        }

    def _write_crop_vars(self, box: tuple[int, int, int, int]) -> None:
        x, y, width, height = box
        self.custom_x_var.set(str(x))
        self.custom_y_var.set(str(y))
        self.custom_w_var.set(str(width))
        self.custom_h_var.set(str(height))

    def _crop_changed(self, value: str) -> None:
        if not self.info or not self.info.has_video:
            return
        width, height = self.info.width, self.info.height
        if value == "Original":
            if self.crop_mode:
                self._leave_crop_mode()
        elif width > 0 and height > 0:
            ratio = crop_ratio(value)
            if ratio is not None:
                # A size preset starts as the largest box of that shape; move or resize it after.
                self.crop_rect = cg.centered(ratio, width, height)
            else:
                self.crop_rect = self._crop_rect_from_vars()
            self._write_crop_vars(cg.finalize(self.crop_rect, width, height))
            if not self.crop_mode:
                self.enter_crop_mode()
            else:
                self._draw_crop_overlay()
        self._sync_crop_entries()
        self._apply_player_video_transform()
        self._update_export_summary()
        self.schedule_preview(delay=40)
        if not self.crop_mode:
            self._record_history_snapshot()

    def _rotation_changed(self, _value: str) -> None:
        self._apply_player_video_transform()
        self._update_export_summary()
        self.schedule_preview(delay=20)
        self._record_history_snapshot()

    def rotate_step(self, direction: int) -> None:
        if self.render_busy or not self.info or not self.info.has_video:
            return
        try:
            index = ROTATIONS.index(self.rotate_var.get())
        except ValueError:
            index = 0
        self.rotate_var.set(ROTATIONS[(index + direction) % len(ROTATIONS)])
        self._rotation_changed(self.rotate_var.get())

    def _flip_changed(self) -> None:
        self._apply_player_video_transform()
        self.schedule_preview(delay=20)
        self._record_history_snapshot()

    def _update_color_labels(self) -> None:
        for key, variable in (
            ("brightness", self.brightness_var),
            ("contrast", self.contrast_var),
            ("saturation", self.saturation_var),
        ):
            value = int(round(variable.get()))
            self.color_value_labels[key].configure(text=f"{value:+d}" if value else "0")

    def _color_changed(self, _key: str) -> None:
        self._update_color_labels()
        # Sliders fire on every pixel; update the player at most every 80 ms.
        if self.color_after_id is not None:
            return

        def apply() -> None:
            self.color_after_id = None
            self._apply_player_video_transform()
            self.schedule_preview(delay=60)

        self.color_after_id = self.after(80, apply)

    def _reset_one_color(self, variable: tk.DoubleVar, key: str) -> str:
        if not self.render_busy:
            variable.set(0.0)
            self._color_changed(key)
            self._record_history_snapshot()
        return "break"

    def reset_color(self) -> None:
        if self.render_busy:
            return
        for variable in (self.brightness_var, self.contrast_var, self.saturation_var):
            variable.set(0.0)
        self._color_changed("all")
        self._record_history_snapshot()

    def _resolution_changed(self, _value: str) -> None:
        self._update_export_summary()
        self._record_history_snapshot()

    def _apply_player_video_transform(self) -> None:
        if self.player is None or not self.player.ready or not self.info or not self.info.has_video:
            return
        try:
            filters = build_player_filters(
                self.info,
                self.crop_var.get(),
                self._current_custom_crop(),
                **self._video_adjustments(),
            )
            rotate_map = {"0°": 0, "90°": 90, "180°": 180, "270°": 270}
            self.player.set_video_transform(
                crop=None,
                rotate_degrees=rotate_map.get(self.rotate_var.get(), 0),
                filters=filters,
            )
        except Exception as exc:
            self.status_label.configure(text=f"Preview transform error: {exc}", text_color=WARNING)

    def _current_custom_crop(self) -> tuple[int, int, int, int] | None:
        if self.crop_var.get() == "Original":
            return None
        if not self.custom_w_var.get() or not self.custom_h_var.get():
            return None
        return (
            int(float(self.custom_x_var.get() or 0)),
            int(float(self.custom_y_var.get() or 0)),
            int(float(self.custom_w_var.get() or 0)),
            int(float(self.custom_h_var.get() or 0)),
        )

    def apply_custom_crop(self, silent: bool = False) -> None:
        if self.render_busy or self._applying_history:
            return
        if not self.info or self.crop_var.get() == "Original":
            return
        try:
            crop = self._current_custom_crop()
            compute_crop(self.info, "Custom", crop)
            if self.crop_mode:
                self.crop_rect = self._crop_rect_from_vars()
                self._draw_crop_overlay()
            self._apply_player_video_transform()
            self._update_export_summary()
            self.schedule_preview(delay=20)
            self._record_history_snapshot()
            if not silent:
                self.status_label.configure(text="Crop numbers applied", text_color=SUCCESS)
        except Exception as exc:
            if silent:
                self.status_label.configure(text=f"Crop numbers not applied: {exc}", text_color=DANGER)
            else:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)

    # ------------------------------------------------------------------ crop box on the video
    def _crop_rect_from_vars(self) -> cg.Rect:
        info = self.info
        assert info is not None
        try:
            box = compute_crop(info, self.crop_var.get(), self._current_custom_crop())
        except Exception:
            box = None
        if box:
            return tuple(float(v) for v in box)  # type: ignore[return-value]
        return cg.centered(crop_ratio(self.crop_var.get()), info.width, info.height)

    def toggle_crop_mode(self) -> None:
        if self.crop_mode:
            self.exit_crop_mode(apply=True)
        else:
            self.enter_crop_mode()

    def enter_crop_mode(self) -> None:
        info = self.info
        if self.crop_mode or self.render_busy or not info or not info.has_video:
            return
        if info.width <= 0 or info.height <= 0:
            messagebox.showwarning(APP_TITLE, "The video size could not be detected, so it cannot be cropped.",
                                   parent=self)
            return
        self.pause_playback()
        # Cancel goes back to the last committed edit (the size menu may already have changed).
        last = self.history.current
        if last is not None:
            self.crop_saved = (last.crop, last.custom_x, last.custom_y, last.custom_w, last.custom_h)
        else:
            self.crop_saved = (
                self.crop_var.get(),
                self.custom_x_var.get(),
                self.custom_y_var.get(),
                self.custom_w_var.get(),
                self.custom_h_var.get(),
            )
        if self.crop_var.get() == "Original":
            self.crop_var.set("Custom")
            self._write_crop_vars(cg.finalize(cg.full_frame(info.width, info.height), info.width, info.height))
        self.crop_rect = self._crop_rect_from_vars()
        self.crop_mode = True
        self.crop_drag = None
        if self.preview_after_id is not None:
            try:
                self.after_cancel(self.preview_after_id)
            except Exception:
                pass
            self.preview_after_id = None
        self.preview_generation += 1
        self._show_video_host(False)
        self.transport_bar.grid_remove()
        self.crop_bar.grid(row=1, column=0, sticky="ew", pady=(native.GAP, 0))
        self.tabs.set("Video")
        self._sync_crop_entries()
        self.preview_canvas.focus_set()
        self.status_label.configure(
            text="Crop: drag the box to move it, its corners or edges to resize, or outside it for a new box. "
                 "Enter = Done, Esc = Cancel, arrows nudge.",
            text_color=MUTED,
        )
        self._draw_crop_overlay()
        self._request_crop_frame(delay=0)

    def exit_crop_mode(self, apply: bool = True) -> None:
        if not self.crop_mode:
            return
        info = self.info
        if not apply and self.crop_saved is not None:
            preset, x, y, w, h = self.crop_saved
            self.crop_var.set(preset)
            self.custom_x_var.set(x)
            self.custom_y_var.set(y)
            self.custom_w_var.set(w)
            self.custom_h_var.set(h)
        elif info and self.crop_rect is not None:
            box = cg.finalize(self.crop_rect, info.width, info.height)
            if self.crop_var.get() == "Custom" and box == (0, 0, info.width // 2 * 2, info.height // 2 * 2):
                # The box still covers the whole frame: that is no crop at all.
                self.crop_var.set("Original")
            self._write_crop_vars(box)
        self._leave_crop_mode()
        self._sync_crop_entries()
        self._apply_player_video_transform()
        self._update_export_summary()
        self.schedule_preview(delay=20)
        if apply:
            self._record_history_snapshot()
            crop = None
            if info:
                try:
                    crop = compute_crop(info, self.crop_var.get(), self._current_custom_crop())
                except Exception:
                    crop = None
            text = f"Crop set to {crop[2]}×{crop[3]}" if crop else "Crop removed"
            self.status_label.configure(text=text, text_color=SUCCESS)
        else:
            self.status_label.configure(text="Crop change cancelled", text_color=MUTED)

    def _leave_crop_mode(self) -> None:
        self.crop_mode = False
        self.crop_drag = None
        self.crop_saved = None
        self.crop_frame_generation += 1
        self.crop_bar.grid_remove()
        self.transport_bar.grid()
        self.preview_canvas.configure(cursor="crosshair")
        self.preview_canvas.delete("all")
        if self.player is not None and self.player.ready:
            self._show_video_host(True)
            try:
                self.player.seek(float(self.playhead_var.get()))
            except Exception:
                pass
        elif self.preview_pil is not None:
            self._draw_preview_image(self.preview_pil)

    def _request_crop_frame(self, delay: int = 120) -> None:
        if not self.crop_mode:
            return
        if self.crop_frame_after_id is not None:
            try:
                self.after_cancel(self.crop_frame_after_id)
            except Exception:
                pass
        self.crop_frame_after_id = self.after(max(1, delay), self._start_crop_frame)

    def _start_crop_frame(self) -> None:
        self.crop_frame_after_id = None
        if not self.crop_mode:
            return
        self.crop_frame_generation += 1
        generation = self.crop_frame_generation
        position = float(self.playhead_var.get())
        width, height = CROP_FRAME_SIZE
        filters = [f"scale={width}:{height}:force_original_aspect_ratio=decrease"]
        threading.Thread(target=self._crop_frame_worker, args=(generation, position, filters), daemon=True).start()

    def _crop_frame_worker(self, generation: int, position: float, filters: list[str]) -> None:
        try:
            data = extract_preview_frame(self.source_path, position, filters, timeout=10)
            image = Image.open(io.BytesIO(data)).convert("RGB")
            self._post_ui(self._apply_crop_frame, generation, image)
        except Exception as exc:
            LOGGER.warning("Crop frame failed source=%s error=%s", self.source_path, exc)

    def _apply_crop_frame(self, generation: int, image: Image.Image) -> None:
        if generation != self.crop_frame_generation or not self.crop_mode:
            return
        self.crop_frame_pil = image
        self.crop_frame_photo = None
        self._draw_crop_overlay()

    def _crop_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        ox, oy, scale = self.crop_view or (0.0, 0.0, 1.0)
        return ox + x * scale, oy + y * scale

    def _canvas_to_crop(self, x: float, y: float) -> tuple[float, float]:
        ox, oy, scale = self.crop_view or (0.0, 0.0, 1.0)
        return (x - ox) / scale, (y - oy) / scale

    def _draw_crop_overlay(self) -> None:
        if not self.crop_mode or not self.info or self.crop_rect is None:
            return
        canvas = self.preview_canvas
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        frame_w, frame_h = self.info.width, self.info.height
        margin = px(18)
        scale = min((cw - 2 * margin) / frame_w, (ch - 2 * margin) / frame_h)
        if scale <= 0:
            return
        dw, dh = frame_w * scale, frame_h * scale
        ox, oy = (cw - dw) / 2, (ch - dh) / 2
        self.crop_view = (ox, oy, scale)

        left, top = self._crop_to_canvas(self.crop_rect[0], self.crop_rect[1])
        right, bottom = self._crop_to_canvas(self.crop_rect[0] + self.crop_rect[2],
                                             self.crop_rect[1] + self.crop_rect[3])

        size = (max(1, int(round(dw))), max(1, int(round(dh))))
        if self.crop_frame_pil is not None:
            # The whole frame darkened, with the kept part drawn bright on top.
            if self.crop_frame_photo is None or self.crop_frame_photo_size != size:
                self.crop_frame_scaled = self.crop_frame_pil.resize(size, Image.Resampling.BILINEAR)
                dim = ImageEnhance.Brightness(self.crop_frame_scaled).enhance(0.35)
                self.crop_frame_photo = ImageTk.PhotoImage(dim)
                self.crop_frame_photo_size = size
            canvas.create_image(ox, oy, image=self.crop_frame_photo, anchor="nw")
            box_l = max(0, int(round(left - ox)))
            box_t = max(0, int(round(top - oy)))
            box_r = min(size[0], int(round(right - ox)))
            box_b = min(size[1], int(round(bottom - oy)))
            if box_r > box_l and box_b > box_t:
                self.crop_bright_photo = ImageTk.PhotoImage(self.crop_frame_scaled.crop((box_l, box_t, box_r, box_b)))
                canvas.create_image(ox + box_l, oy + box_t, image=self.crop_bright_photo, anchor="nw")
        else:
            canvas.create_rectangle(ox, oy, ox + dw, oy + dh, fill="#1E1E1E", outline="")
            canvas.create_text(ox + dw / 2, oy + dh / 2, text="Loading frame…", fill="#C0C0C0",
                               font=native.font(self))
        # Rule-of-thirds guides.
        for i in (1, 2):
            gx = left + (right - left) * i / 3
            gy = top + (bottom - top) * i / 3
            canvas.create_line(gx, top, gx, bottom, fill="#FFFFFF", dash=(3, 4))
            canvas.create_line(left, gy, right, gy, fill="#FFFFFF", dash=(3, 4))
        canvas.create_rectangle(left - 1, top - 1, right + 1, bottom + 1, outline="#000000", width=1)
        canvas.create_rectangle(left, top, right, bottom, outline="#FFFFFF", width=2)

        # Handles: corners and edge midpoints.
        handle = px(5)
        mid_x, mid_y = (left + right) / 2, (top + bottom) / 2
        for hx, hy in ((left, top), (mid_x, top), (right, top), (right, mid_y),
                       (right, bottom), (mid_x, bottom), (left, bottom), (left, mid_y)):
            canvas.create_rectangle(hx - handle, hy - handle, hx + handle, hy + handle,
                                    fill="#FFFFFF", outline="#000000")

        box = cg.finalize(self.crop_rect, frame_w, frame_h)
        text = f"{box[2]} × {box[3]}"
        label = canvas.create_text(left + px(8), top + px(8), text=text, fill="#FFFFFF", anchor="nw",
                                   font=native.font(self, bold=True))
        x1, y1, x2, y2 = canvas.bbox(label)
        backing = canvas.create_rectangle(x1 - 4, y1 - 2, x2 + 4, y2 + 2, fill="#000000", outline="")
        canvas.tag_lower(backing, label)

        preset = self.crop_var.get()
        self.crop_size_label.configure(text=f"{box[2]}×{box[3]}" if crop_ratio(preset) else f"{box[2]}×{box[3]} free")

    def _crop_press(self, event: Any) -> str | None:
        if not self.crop_mode or self.crop_rect is None or not self.info or self.crop_view is None:
            return None
        self.preview_canvas.focus_set()
        sx, sy = self._canvas_to_crop(event.x, event.y)
        scale = self.crop_view[2]
        handle = cg.hit_test(self.crop_rect, sx, sy, px(9) / scale)
        self.crop_drag = {
            "handle": handle or "new",
            "start": (sx, sy),
            "rect": self.crop_rect,
            "moved": False,
        }
        return "break"

    def _crop_motion(self, event: Any) -> str | None:
        drag = self.crop_drag
        if not self.crop_mode or drag is None or not self.info:
            return None
        frame_w, frame_h = self.info.width, self.info.height
        sx, sy = self._canvas_to_crop(event.x, event.y)
        ax, ay = drag["start"]
        ratio = crop_ratio(self.crop_var.get())
        if drag["handle"] == "new":
            if not drag["moved"] and max(abs(sx - ax), abs(sy - ay)) * (self.crop_view or (0, 0, 1))[2] < 4:
                return "break"
            rect = cg.from_points(ax, ay, sx, sy, frame_w, frame_h, ratio)
            if rect[2] < 2 or rect[3] < 2:
                return "break"
        else:
            rect = cg.resize(drag["rect"], drag["handle"], sx - ax, sy - ay, frame_w, frame_h, ratio)
        drag["moved"] = True
        self.crop_rect = rect
        self._write_crop_vars(cg.finalize(rect, frame_w, frame_h))
        self._draw_crop_overlay()
        self._update_export_summary()
        return "break"

    def _crop_release(self, _event: Any) -> str | None:
        drag = self.crop_drag
        if drag is None:
            return None
        self.crop_drag = None
        if drag["handle"] == "new" and self.crop_rect is not None:
            if self.crop_rect[2] < cg.MIN_SIZE or self.crop_rect[3] < cg.MIN_SIZE:
                self.crop_rect = drag["rect"]  # a click, or a box too small to be useful
        if self.info and self.crop_rect is not None:
            self._write_crop_vars(cg.finalize(self.crop_rect, self.info.width, self.info.height))
        self._draw_crop_overlay()
        self._update_export_summary()
        return "break"

    def _crop_hover(self, event: Any) -> None:
        if not self.crop_mode or self.crop_rect is None or self.crop_view is None or self.crop_drag is not None:
            return
        sx, sy = self._canvas_to_crop(event.x, event.y)
        handle = cg.hit_test(self.crop_rect, sx, sy, px(9) / self.crop_view[2])
        try:
            self.preview_canvas.configure(cursor=CROP_CURSORS.get(handle or "", "crosshair"))
        except tk.TclError:
            self.preview_canvas.configure(cursor="crosshair")

    def _crop_double_click(self, event: Any) -> str | None:
        if self.crop_mode:
            if self.crop_rect is not None and self.crop_view is not None:
                sx, sy = self._canvas_to_crop(event.x, event.y)
                if cg.hit_test(self.crop_rect, sx, sy, 0) == "move":
                    self.exit_crop_mode(apply=True)
            return "break"
        if self.info and self.info.has_video and not self.render_busy:
            self.enter_crop_mode()
            return "break"
        return None

    def _set_crop_rect(self, rect: cg.Rect) -> None:
        if not self.info:
            return
        self.crop_rect = rect
        self._write_crop_vars(cg.finalize(rect, self.info.width, self.info.height))
        self._draw_crop_overlay()
        self._update_export_summary()

    def crop_center(self) -> None:
        if self.crop_mode and self.info and self.crop_rect is not None:
            _x, _y, w, h = self.crop_rect
            self._set_crop_rect(((self.info.width - w) / 2, (self.info.height - h) / 2, w, h))

    def crop_max_size(self) -> None:
        if self.crop_mode and self.info:
            self._set_crop_rect(cg.centered(crop_ratio(self.crop_var.get()), self.info.width, self.info.height))

    def crop_nudge(self, dx: float, dy: float) -> None:
        if self.crop_mode and self.info and self.crop_rect is not None:
            self._set_crop_rect(cg.nudge(self.crop_rect, dx, dy, self.info.width, self.info.height))

    def _on_vertical_arrow(self, _event: Any, direction: int, coarse: bool) -> str | None:
        if not self.crop_mode or not self._shortcut_allowed():
            return None
        self.crop_nudge(0, direction * (10 if coarse else 1))
        return "break"

    def _on_crop_return(self, _event: Any) -> str | None:
        if not self.crop_mode or not self._shortcut_allowed():
            return None
        self.exit_crop_mode(apply=True)
        return "break"

    def _on_crop_escape(self, _event: Any) -> str | None:
        if not self.crop_mode:
            return None
        if self.crop_drag is not None:
            self.crop_rect = self.crop_drag["rect"]
            self.crop_drag = None
            self._set_crop_rect(self.crop_rect)
            return "break"
        self.exit_crop_mode(apply=False)
        return "break"

    def _on_crop_shortcut(self, _event: Any) -> str | None:
        if not self._shortcut_allowed() or self.render_busy:
            return None
        self.toggle_crop_mode()
        return "break"

    def _speed_value(self) -> float:
        return float(self.speed_var.get().rstrip("x"))

    def _volume_changed(self, value: float) -> None:
        self.volume_text.configure(text=f"Volume {int(float(value))}%")
        if self.player is not None:
            self.player.set_volume(float(value) / 100.0)
            self.player.set_mute(self.mute_var.get())

    def _sync_audio_state(self) -> None:
        if not self.info or not self.info.has_audio:
            return
        state = "disabled" if self.mute_var.get() else "normal"
        self.volume_slider.configure(state=state)
        self.fade_in_entry.configure(state=state)
        self.fade_out_entry.configure(state=state)
        if self.player is not None:
            self.player.set_volume(self.volume_var.get() / 100.0)
            self.player.set_mute(self.mute_var.get())
        self._record_history_snapshot()

    def _speed_changed(self, _value: str) -> None:
        if self.player is not None:
            supported = self.player.set_rate(self._speed_value())
            self.player_rate_supported = supported
            if supported:
                self.preview_status_label.configure(text=f"Playback speed: {self.speed_var.get()}", text_color=MUTED)
            else:
                self.preview_status_label.configure(text="Speed change will apply on export", text_color=WARNING)
        self._update_export_summary()
        self._record_history_snapshot()

    def reset_video_edits(self) -> None:
        if self.render_busy:
            return
        previous = self._applying_history
        self._applying_history = True
        try:
            if self.crop_mode:
                self.crop_var.set("Original")
                self._leave_crop_mode()
            self.crop_var.set("Original")
            self.rotate_var.set("0°")
            self.speed_var.set("1.0x")
            self._speed_changed("1.0x")
            self.flip_h_var.set(False)
            self.flip_v_var.set(False)
            for variable in (self.brightness_var, self.contrast_var, self.saturation_var):
                variable.set(0.0)
            self._update_color_labels()
            self.resolution_var.set("Original")
            self._sync_crop_entries()
            if self.info:
                self.custom_x_var.set("0")
                self.custom_y_var.set("0")
                self.custom_w_var.set(str(self.info.width))
                self.custom_h_var.set(str(self.info.height))
            self._apply_player_video_transform()
            self._update_export_summary()
            self.schedule_preview(delay=40)
        finally:
            self._applying_history = previous
        if not previous:
            self._record_history_snapshot()

    def reset_audio_edits(self) -> None:
        if self.render_busy:
            return
        previous = self._applying_history
        self._applying_history = True
        try:
            self.mute_var.set(False)
            self.volume_var.set(100)
            self.speed_var.set("1.0x")
            self._speed_changed("1.0x")
            self.fade_in_var.set("0")
            self.fade_out_var.set("0")
            self._volume_changed(100)
            self._sync_audio_state()
            self._update_export_summary()
        finally:
            self._applying_history = previous
        if not previous:
            self._record_history_snapshot()

    def reset_edits(self) -> None:
        if self.render_busy:
            return
        previous = self._applying_history
        self._applying_history = True
        try:
            self.reset_video_edits()
            self.reset_audio_edits()
            self.reset_range()
        finally:
            self._applying_history = previous
        if not previous:
            self._record_history_snapshot()
        self.status_label.configure(text="All edits reset", text_color=MUTED)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)

    def _validated_settings(self) -> dict[str, Any]:
        if not self.info:
            raise RuntimeError("Media is still loading.")
        if self.crop_mode:
            self.exit_crop_mode(apply=True)

        start = parse_time(self.start_var.get())
        end = parse_time(self.end_var.get())
        start = max(0.0, min(start, self.info.duration))
        end = max(0.0, min(end, self.info.duration))
        if end <= start:
            raise ValueError("OUT must be after IN.")

        speed = self._speed_value()
        fade_in = max(0.0, float(self.fade_in_var.get() or 0))
        fade_out = max(0.0, float(self.fade_out_var.get() or 0))
        custom_crop = self._current_custom_crop()
        if self.info.has_video:
            compute_crop(self.info, self.crop_var.get(), custom_crop)

        return {
            "start": start,
            "end": end,
            "crop_preset": self.crop_var.get(),
            "custom_crop": custom_crop,
            "rotate": self.rotate_var.get(),
            "speed": speed,
            "mute": bool(self.mute_var.get()),
            "volume_percent": float(self.volume_var.get()),
            "fade_in": fade_in,
            "fade_out": fade_out,
            "quality": self.quality_var.get(),
            "video": {**self._video_adjustments(), "resolution": self.resolution_var.get()},
        }

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose output folder",
            initialdir=str(self.output_dir if self.output_dir.exists() else self.output_dir.parent),
            parent=self,
        )
        if selected:
            self.output_dir = Path(selected)
            self.output_dir_label.configure(text=_short_path(self.output_dir))

    def _build_output_path(self) -> Path:
        name = safe_export_name(self.output_name_var.get(), f"{self.source_path.stem}_edited")
        extension = "." + self.format_var.get().lower()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        candidate = self.output_dir / f"{name}{extension}"
        try:
            if candidate.resolve() == self.source_path.resolve():
                candidate = self.output_dir / f"{name}_edited{extension}"
        except Exception:
            pass

        if not candidate.exists():
            return candidate

        index = 2
        while True:
            alt = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
            if not alt.exists():
                return alt
            index += 1

    def preview_edit_clip(self) -> None:
        if self.preview_clip_busy or self.export_process is not None or self.render_busy:
            return
        try:
            settings = self._validated_settings()
            if not self.info:
                return
            start = float(self.playhead_var.get())
            if start < settings["start"] or start >= settings["end"]:
                start = settings["start"]
            duration = min(15.0, settings["end"] - start)
            suffix = ".mp4" if self.info.has_video else ".mp3"
            output = self.temp_dir / f"preview_{os.getpid()}{suffix}"
            command = build_preview_clip_command(
                self.source_path,
                output,
                self.info,
                start=start,
                duration=duration,
                crop_preset=settings["crop_preset"],
                custom_crop=settings["custom_crop"],
                rotate=settings["rotate"],
                speed=settings["speed"],
                mute=settings["mute"],
                volume_percent=settings["volume_percent"],
                fade_in=settings["fade_in"],
                fade_out=settings["fade_out"],
                **settings["video"],
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        self.preview_clip_busy = True
        self._set_render_busy(True)
        self.preview_clip_button.configure(state="disabled", text="Rendering preview…")
        self.export_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_label.configure(text="Rendering a 15-second edited preview…", text_color=WARNING)

        threading.Thread(
            target=self._preview_clip_worker,
            args=(command, output),
            daemon=True,
        ).start()

    def _preview_clip_worker(self, command: list[str], output: Path) -> None:
        try:
            output.unlink(missing_ok=True)
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=60,
            )
            if result.returncode != 0 or not output.exists():
                raise RuntimeError((result.stderr or "Preview render failed.")[-1600:])
            self._post_ui(self._preview_clip_done, output)
        except Exception as exc:
            self._post_ui(self._preview_clip_failed, str(exc))

    def _preview_clip_done(self, output: Path) -> None:
        self.preview_clip_busy = False
        self._set_render_busy(False)
        LOGGER.info("Edited preview rendered source=%s output=%s", self.source_path, output)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.preview_clip_button.configure(state="normal", text="Preview edit (15s)")
        self.export_button.configure(state="normal")
        self.status_label.configure(text="Preview rendered. Opening in your media player…", text_color=SUCCESS)
        try:
            _open_path(output)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _preview_clip_failed(self, error: str) -> None:
        self.preview_clip_busy = False
        self._set_render_busy(False)
        LOGGER.error("Edited preview failed source=%s error=%s", self.source_path, error)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.preview_clip_button.configure(state="normal", text="Preview edit (15s)")
        self.export_button.configure(state="normal")
        self.status_label.configure(text="Preview render failed", text_color=DANGER)
        messagebox.showerror(APP_TITLE, error, parent=self)

    def export_media(self) -> None:
        if self.export_process is not None or self.preview_clip_busy or self.render_busy:
            return
        try:
            settings = self._validated_settings()
            if not self.info:
                return
            output = self._build_output_path()
            command, expected_duration = build_export_command(
                self.source_path,
                output,
                self.info,
                start=settings["start"],
                end=settings["end"],
                crop_preset=settings["crop_preset"],
                custom_crop=settings["custom_crop"],
                rotate=settings["rotate"],
                speed=settings["speed"],
                mute=settings["mute"],
                volume_percent=settings["volume_percent"],
                fade_in=settings["fade_in"],
                fade_out=settings["fade_out"],
                quality=settings["quality"],
                **settings["video"],
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        self.export_cancelled = False
        self._set_render_busy(True)
        self.export_button.configure(state="disabled", text="Exporting…")
        self.preview_clip_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_export_button.configure(state="disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.status_label.configure(text=f"Exporting {output.name}…", text_color=WARNING)

        threading.Thread(
            target=self._export_worker,
            args=(command, output, expected_duration),
            daemon=True,
        ).start()

    def _export_worker(self, command: list[str], output: Path, expected_duration: float) -> None:
        error_lines: list[str] = []
        try:
            output.unlink(missing_ok=True)
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            self.export_process = process

            if process.stdout is not None:
                for raw in process.stdout:
                    line = raw.strip()
                    if not line:
                        continue
                    if "=" not in line:
                        error_lines.append(line)
                        error_lines = error_lines[-20:]
                        continue
                    key, value = line.split("=", 1)
                    if key in {"out_time_ms", "out_time_us"}:
                        try:
                            seconds = int(value) / 1_000_000
                            fraction = max(0.0, min(1.0, seconds / max(0.001, expected_duration)))
                            self._post_ui(self._apply_export_progress, fraction)
                        except Exception:
                            pass

            returncode = process.wait()
            cancelled = self.export_cancelled
            self.export_process = None

            if cancelled:
                try:
                    output.unlink(missing_ok=True)
                except Exception:
                    pass
                self._post_ui(self._export_cancelled_ui)
                return

            if returncode != 0 or not output.exists():
                raise RuntimeError("\n".join(error_lines)[-1800:] or "FFmpeg export failed.")

            self._post_ui(self._export_done, output)
        except Exception as exc:
            self.export_process = None
            self._post_ui(self._export_failed, str(exc))

    def _apply_export_progress(self, fraction: float) -> None:
        self.progress.set(fraction)
        self.status_label.configure(text=f"Exporting… {fraction * 100:.0f}%", text_color=WARNING)

    def cancel_export(self) -> None:
        process = self.export_process
        if process is None:
            return
        self.export_cancelled = True
        self.status_label.configure(text="Cancelling export…", text_color=WARNING)
        try:
            process.terminate()
        except Exception:
            pass

    def _export_cancelled_ui(self) -> None:
        self._set_render_busy(False)
        LOGGER.info("Export cancelled source=%s", self.source_path)
        self.progress.set(0)
        self.export_button.configure(state="normal", text="Export…")
        self.preview_clip_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="Export cancelled", text_color=MUTED)

    def _export_done(self, output: Path) -> None:
        self._set_render_busy(False)
        self.last_export = output
        LOGGER.info("Export completed source=%s output=%s", self.source_path, output)
        self.progress.set(1)
        self.export_button.configure(state="normal", text="Export…")
        self.preview_clip_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.open_export_button.configure(state="normal")
        self.status_label.configure(text=f"Export complete • {output.name}", text_color=SUCCESS)

        try:
            self.parent.last_file = output
            self.parent.open_file_button.configure(state="normal")
        except Exception:
            pass

        messagebox.showinfo(
            APP_TITLE,
            f"Export completed successfully.\n\n{output}",
            parent=self,
        )

    def _export_failed(self, error: str) -> None:
        self._set_render_busy(False)
        LOGGER.error("Export failed source=%s error=%s", self.source_path, error)
        self.progress.set(0)
        self.export_button.configure(state="normal", text="Export…")
        self.preview_clip_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="Export failed", text_color=DANGER)
        messagebox.showerror(APP_TITLE, error, parent=self)

    def open_export(self) -> None:
        if self.last_export and self.last_export.exists():
            try:
                _open_path(self.last_export)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def save_current_frame(self) -> None:
        info = self.info
        if not info or not info.has_video or self.render_busy:
            return
        try:
            settings = self._validated_settings()
            position = float(self.playhead_var.get())
            filters = build_video_filters(
                info,
                settings["crop_preset"],
                settings["custom_crop"],
                settings["rotate"],
                1.0,
                include_speed=False,
                **settings["video"],
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        stamp = format_time(position).replace(":", "-").replace(".", "_")
        target = filedialog.asksaveasfilename(
            title="Save current frame",
            parent=self,
            initialdir=str(self.output_dir if self.output_dir.exists() else self.source_path.parent),
            initialfile=f"{safe_export_name(self.source_path.stem)}_frame_{stamp}.png",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg *.jpeg")],
        )
        if not target:
            return
        output = Path(target)
        command = build_frame_export_command(self.source_path, output, position, filters)
        self.status_label.configure(text="Saving frame…", text_color=WARNING)

        def worker() -> None:
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
                                        timeout=30)
                if result.returncode != 0 or not output.exists():
                    raise RuntimeError((result.stderr or "Could not save the frame.")[-1200:])
                self._post_ui(lambda: self.status_label.configure(text=f"Frame saved • {output.name}",
                                                                  text_color=SUCCESS))
            except Exception as exc:
                message = str(exc)
                self._post_ui(lambda: (self.status_label.configure(text="Could not save the frame",
                                                                   text_color=DANGER),
                                       messagebox.showerror(APP_TITLE, message, parent=self)))

        threading.Thread(target=worker, daemon=True).start()

    def open_source(self) -> None:
        try:
            _open_path(self.source_path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _close(self) -> None:
        if self.export_process is not None:
            if not messagebox.askyesno(
                APP_TITLE,
                "An export is still running. Cancel it and close the editor?",
                parent=self,
            ):
                return
            self.cancel_export()
        self.thumbnail_generation += 1
        self.preview_generation += 1
        self._closing = True
        if self.player_tick_id is not None:
            try:
                self.after_cancel(self.player_tick_id)
            except Exception:
                pass
            self.player_tick_id = None
        if self.player is not None:
            try:
                self.player.close()
            except Exception:
                pass
            self.player = None
        self.destroy()
