from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox

from media_editor_engine import (
    CROP_PRESETS,
    CREATE_NO_WINDOW,
    MediaInfo,
    build_audio_filters,
    build_export_command,
    build_preview_clip_command,
    build_video_filters,
    compute_crop,
    extract_preview_frame,
    extract_waveform,
    format_time,
    parse_time,
    probe_media,
    safe_export_name,
)
from media_editor_widgets import TimelineCanvas
from media_player import EmbeddedMediaPlayer, PlayerUnavailableError


APP_TITLE = "Media Editor"

BG = "#060B14"
SURFACE = "#0B1323"
SURFACE_2 = "#101C31"
SURFACE_3 = "#14223A"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
CYAN = "#23D5FF"
CYAN_HOVER = "#0EBDE9"
PURPLE = "#7657FF"
PURPLE_HOVER = "#6547E9"
SUCCESS = "#24D18C"
WARNING = "#FFB84D"
DANGER = "#FF647C"

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}


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


class MediaEditorWindow(ctk.CTkToplevel):
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
        self.last_export: Path | None = None
        self.ui_queue: queue.Queue[tuple[Any, tuple[Any, ...]]] = queue.Queue()
        self._closing = False

        self.temp_dir = Path(tempfile.gettempdir()) / "MediaDownloaderEditor"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.start_var = ctk.StringVar(value="00:00.000")
        self.end_var = ctk.StringVar(value="")
        self.playhead_var = ctk.DoubleVar(value=0.0)

        self.crop_var = ctk.StringVar(value="Original")
        self.rotate_var = ctk.StringVar(value="0°")
        self.speed_var = ctk.StringVar(value="1.0x")
        self.custom_x_var = ctk.StringVar(value="0")
        self.custom_y_var = ctk.StringVar(value="0")
        self.custom_w_var = ctk.StringVar(value="")
        self.custom_h_var = ctk.StringVar(value="")

        self.mute_var = ctk.BooleanVar(value=False)
        self.volume_var = ctk.DoubleVar(value=100.0)
        self.fade_in_var = ctk.StringVar(value="0")
        self.fade_out_var = ctk.StringVar(value="0")

        self.output_name_var = ctk.StringVar(value=f"{self.source_path.stem}_edited")
        self.format_var = ctk.StringVar(value="MP4")
        self.quality_var = ctk.StringVar(value="High")

        self.title(APP_TITLE)
        self.geometry("1280x840")
        self.minsize(1100, 740)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_ui()
        self.bind("<space>", self._on_space)
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

    def _card(self, master: Any, **kwargs: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            master,
            fg_color=SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            **kwargs,
        )

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()

        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.grid(row=1, column=0, sticky="nsew", padx=18, pady=(14, 10))
        workspace.grid_columnconfigure(0, weight=5)
        workspace.grid_columnconfigure(1, weight=2)
        workspace.grid_rowconfigure(0, weight=1)

        self._build_preview(workspace)
        self._build_inspector(workspace)

        self._build_timeline()
        self._build_bottom_bar()

    def _build_topbar(self) -> None:
        top = ctk.CTkFrame(self, height=62, corner_radius=0, fg_color=SURFACE)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top,
            text="ME",
            width=38,
            height=38,
            corner_radius=10,
            fg_color=PURPLE,
            text_color=TEXT,
            font=("Segoe UI Semibold", 13),
        ).grid(row=0, column=0, padx=(18, 12), pady=12)

        title_wrap = ctk.CTkFrame(top, fg_color="transparent")
        title_wrap.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            title_wrap,
            text="MEDIA EDITOR",
            text_color=TEXT,
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w")
        self.source_label = ctk.CTkLabel(
            title_wrap,
            text=self.source_path.name,
            text_color=MUTED,
            font=("Segoe UI", 9),
        )
        self.source_label.pack(anchor="w", pady=(2, 0))

        self.loading_chip = ctk.CTkLabel(
            top,
            text="LOADING",
            height=28,
            corner_radius=9,
            fg_color="#2D2514",
            text_color=WARNING,
            font=("Segoe UI Semibold", 9),
        )
        self.loading_chip.grid(row=0, column=2, padx=(8, 8))

        ctk.CTkButton(
            top,
            text="Reset edits",
            width=105,
            height=34,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.reset_edits,
        ).grid(row=0, column=3, padx=(8, 0))

        ctk.CTkButton(
            top,
            text="Open source",
            width=105,
            height=34,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.open_source,
        ).grid(row=0, column=4, padx=(8, 18))

    def _build_preview(self, workspace: ctk.CTkFrame) -> None:
        card = self._card(workspace)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        preview_wrap = ctk.CTkFrame(card, fg_color="#030812", corner_radius=11)
        preview_wrap.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        preview_wrap.grid_columnconfigure(0, weight=1)
        preview_wrap.grid_rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            preview_wrap,
            bg="#030812",
            bd=0,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)
        self.preview_canvas.create_text(
            20,
            20,
            text="Loading media…",
            fill=MUTED,
            anchor="nw",
            font=("Segoe UI", 12),
            tags="placeholder",
        )

        transport = ctk.CTkFrame(card, fg_color="transparent")
        transport.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        transport.grid_columnconfigure(5, weight=1)

        ctk.CTkButton(
            transport,
            text="◀ 5s",
            width=68,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=lambda: self.step_playhead(-5.0),
        ).grid(row=0, column=0, padx=(0, 5))

        ctk.CTkButton(
            transport,
            text="Set In",
            width=72,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=self.set_in_here,
        ).grid(row=0, column=1, padx=5)

        self.play_button = ctk.CTkButton(
            transport,
            text="▶ Play",
            width=88,
            height=36,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            text_color=TEXT,
            font=("Segoe UI Semibold", 10),
            command=self.toggle_playback,
            state="disabled",
        )
        self.play_button.grid(row=0, column=2, padx=5)

        self.current_time_label = ctk.CTkLabel(
            transport,
            text="00:00.000 / --:--",
            text_color=TEXT,
            font=("Segoe UI Semibold", 10),
        )
        self.current_time_label.grid(row=0, column=3, padx=10)

        ctk.CTkButton(
            transport,
            text="Set Out",
            width=76,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=self.set_out_here,
        ).grid(row=0, column=4, padx=5)

        self.preview_status_label = ctk.CTkLabel(
            transport,
            text="Space = Play/Pause",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.preview_status_label.grid(row=0, column=5, sticky="w", padx=10)

        ctk.CTkButton(
            transport,
            text="5s ▶",
            width=68,
            height=34,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=lambda: self.step_playhead(5.0),
        ).grid(row=0, column=6, padx=(5, 0))

    def _build_inspector(self, workspace: ctk.CTkFrame) -> None:
        card = self._card(workspace)
        card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(
            card,
            fg_color=SURFACE,
            segmented_button_fg_color=SURFACE_2,
            segmented_button_selected_color=PURPLE,
            segmented_button_selected_hover_color=PURPLE_HOVER,
            segmented_button_unselected_color=SURFACE_2,
            segmented_button_unselected_hover_color=SURFACE_3,
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.tabs.add("Video")
        self.tabs.add("Audio")
        self.tabs.add("Export")

        self._build_video_tab(self.tabs.tab("Video"))
        self._build_audio_tab(self.tabs.tab("Audio"))
        self._build_export_tab(self.tabs.tab("Export"))

    def _field_label(self, master: Any, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(master, text=text, text_color=MUTED, font=("Segoe UI", 9), anchor="w")

    def _build_video_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab,
            text="FRAME & SPEED",
            text_color=CYAN,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="w", pady=(8, 12))

        self._field_label(tab, "Crop / aspect ratio").grid(row=1, column=0, sticky="w")
        self.crop_menu = ctk.CTkOptionMenu(
            tab,
            variable=self.crop_var,
            values=list(CROP_PRESETS),
            command=self._crop_changed,
            fg_color=SURFACE_3,
            button_color=PURPLE,
            button_hover_color=PURPLE_HOVER,
        )
        self.crop_menu.grid(row=2, column=0, sticky="ew", pady=(4, 10))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._field_label(left, "Rotate").pack(anchor="w")
        self.rotate_menu = ctk.CTkOptionMenu(
            left,
            variable=self.rotate_var,
            values=["0°", "90°", "180°", "270°"],
            command=lambda _value: self.schedule_preview(),
            fg_color=SURFACE_3,
            button_color=PURPLE,
        )
        self.rotate_menu.pack(fill="x", pady=(4, 0))

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self._field_label(right, "Speed").pack(anchor="w")
        self.speed_menu = ctk.CTkOptionMenu(
            right,
            variable=self.speed_var,
            values=["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
            command=self._speed_changed,
            fg_color=SURFACE_3,
            button_color=PURPLE,
        )
        self.speed_menu.pack(fill="x", pady=(4, 0))

        custom = ctk.CTkFrame(tab, fg_color=SURFACE_2, corner_radius=10)
        custom.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        for column in range(2):
            custom.grid_columnconfigure(column, weight=1)

        ctk.CTkLabel(
            custom,
            text="CUSTOM CROP",
            text_color=TEXT,
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(9, 6))

        self.custom_entries: list[ctk.CTkEntry] = []
        fields = [
            ("X", self.custom_x_var),
            ("Y", self.custom_y_var),
            ("Width", self.custom_w_var),
            ("Height", self.custom_h_var),
        ]
        for index, (label, variable) in enumerate(fields):
            cell = ctk.CTkFrame(custom, fg_color="transparent")
            cell.grid(row=1 + index // 2, column=index % 2, sticky="ew", padx=(10, 5) if index % 2 == 0 else (5, 10), pady=4)
            self._field_label(cell, label).pack(anchor="w")
            entry = ctk.CTkEntry(cell, textvariable=variable, height=32)
            entry.pack(fill="x", pady=(2, 0))
            entry.configure(state="disabled")
            entry.bind("<Return>", lambda _event: self.apply_custom_crop())
            self.custom_entries.append(entry)

        self.apply_crop_button = ctk.CTkButton(
            custom,
            text="Apply custom crop",
            height=34,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            command=self.apply_custom_crop,
            state="disabled",
        )
        self.apply_crop_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 10))

        ctk.CTkButton(
            tab,
            text="Reset video edits",
            height=36,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.reset_video_edits,
        ).grid(row=5, column=0, sticky="ew", pady=(14, 0))

    def _build_audio_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab,
            text="AUDIO",
            text_color=CYAN,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="w", pady=(8, 12))

        self._field_label(tab, "Playback / export speed").grid(row=1, column=0, sticky="w")
        self.audio_speed_menu = ctk.CTkOptionMenu(
            tab,
            variable=self.speed_var,
            values=["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
            command=self._speed_changed,
            fg_color=SURFACE_3,
            button_color=PURPLE,
        )
        self.audio_speed_menu.grid(row=2, column=0, sticky="ew", pady=(4, 14))

        self.mute_switch = ctk.CTkSwitch(
            tab,
            text="Mute audio",
            variable=self.mute_var,
            text_color=TEXT,
            progress_color=PURPLE,
            button_color=TEXT,
            button_hover_color=CYAN,
            command=self._sync_audio_state,
        )
        self.mute_switch.grid(row=3, column=0, sticky="w", pady=(0, 14))

        self.volume_text = ctk.CTkLabel(
            tab,
            text="Volume 100%",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.volume_text.grid(row=4, column=0, sticky="w")
        self.volume_slider = ctk.CTkSlider(
            tab,
            from_=0,
            to=200,
            variable=self.volume_var,
            number_of_steps=200,
            progress_color=CYAN,
            button_color=TEXT,
            button_hover_color=CYAN,
            command=self._volume_changed,
        )
        self.volume_slider.grid(row=5, column=0, sticky="ew", pady=(5, 16))

        fade = ctk.CTkFrame(tab, fg_color="transparent")
        fade.grid(row=6, column=0, sticky="ew")
        fade.grid_columnconfigure(0, weight=1)
        fade.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(fade, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._field_label(left, "Fade in (sec)").pack(anchor="w")
        self.fade_in_entry = ctk.CTkEntry(left, textvariable=self.fade_in_var, height=34)
        self.fade_in_entry.pack(fill="x", pady=(4, 0))

        right = ctk.CTkFrame(fade, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self._field_label(right, "Fade out (sec)").pack(anchor="w")
        self.fade_out_entry = ctk.CTkEntry(right, textvariable=self.fade_out_var, height=34)
        self.fade_out_entry.pack(fill="x", pady=(4, 0))

        ctk.CTkButton(
            tab,
            text="Reset audio edits",
            height=36,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.reset_audio_edits,
        ).grid(row=7, column=0, sticky="ew", pady=(18, 0))

    def _build_export_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab,
            text="EXPORT",
            text_color=CYAN,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="w", pady=(8, 12))

        self._field_label(tab, "File name").grid(row=1, column=0, sticky="w")
        self.output_name_entry = ctk.CTkEntry(tab, textvariable=self.output_name_var, height=36)
        self.output_name_entry.grid(row=2, column=0, sticky="ew", pady=(4, 12))

        export_row = ctk.CTkFrame(tab, fg_color="transparent")
        export_row.grid(row=3, column=0, sticky="ew")
        export_row.grid_columnconfigure(0, weight=1)
        export_row.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(export_row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._field_label(left, "Format").pack(anchor="w")
        self.format_menu = ctk.CTkOptionMenu(
            left,
            variable=self.format_var,
            values=["MP4", "MKV", "MOV"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        )
        self.format_menu.pack(fill="x", pady=(4, 0))

        right = ctk.CTkFrame(export_row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self._field_label(right, "Quality").pack(anchor="w")
        self.quality_menu = ctk.CTkOptionMenu(
            right,
            variable=self.quality_var,
            values=["High", "Balanced", "Small"],
            fg_color=SURFACE_3,
            button_color=PURPLE,
        )
        self.quality_menu.pack(fill="x", pady=(4, 0))

        ctk.CTkButton(
            tab,
            text="Choose output folder",
            height=36,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=self.choose_output_folder,
        ).grid(row=4, column=0, sticky="ew", pady=(14, 6))

        self.output_dir_label = ctk.CTkLabel(
            tab,
            text=_short_path(self.output_dir),
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.output_dir_label.grid(row=5, column=0, sticky="ew")

        ctk.CTkLabel(
            tab,
            text="Edits are non-destructive. Your source file is never overwritten.",
            text_color="#667996",
            font=("Segoe UI", 9),
            justify="left",
            wraplength=310,
        ).grid(row=6, column=0, sticky="ew", pady=(16, 0))

    def _build_timeline(self) -> None:
        card = self._card(self)
        card.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 4))
        header.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            header,
            text="TIMELINE",
            text_color=CYAN,
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w")

        self.selection_label = ctk.CTkLabel(
            header,
            text="Selection: --",
            text_color=MUTED,
            font=("Segoe UI", 9),
        )
        self.selection_label.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.media_info_label = ctk.CTkLabel(
            header,
            text="",
            text_color="#667996",
            font=("Segoe UI", 9),
        )
        self.media_info_label.grid(row=0, column=3, sticky="e")

        self.timeline = TimelineCanvas(
            card,
            on_seek=self._on_timeline_seek,
            on_range_change=self._on_range_change,
        )
        self.timeline.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))

        range_row = ctk.CTkFrame(card, fg_color="transparent")
        range_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        range_row.grid_columnconfigure(1, weight=1)
        range_row.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(range_row, text="IN", text_color=MUTED, font=("Segoe UI Semibold", 8)).grid(row=0, column=0, padx=(0, 6))
        self.start_entry = ctk.CTkEntry(range_row, textvariable=self.start_var, height=32, width=150)
        self.start_entry.grid(row=0, column=1, sticky="w")
        self.start_entry.bind("<Return>", lambda _event: self.apply_range_entries())
        self.start_entry.bind("<FocusOut>", lambda _event: self.apply_range_entries(silent=True))

        ctk.CTkLabel(range_row, text="OUT", text_color=MUTED, font=("Segoe UI Semibold", 8)).grid(row=0, column=2, padx=(16, 6))
        self.end_entry = ctk.CTkEntry(range_row, textvariable=self.end_var, height=32, width=150)
        self.end_entry.grid(row=0, column=3, sticky="w")
        self.end_entry.bind("<Return>", lambda _event: self.apply_range_entries())
        self.end_entry.bind("<FocusOut>", lambda _event: self.apply_range_entries(silent=True))

        ctk.CTkButton(
            range_row,
            text="Reset range",
            width=95,
            height=32,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.reset_range,
        ).grid(row=0, column=4, padx=(16, 0))

    def _build_bottom_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=62)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        status_wrap = ctk.CTkFrame(bar, fg_color="transparent")
        status_wrap.grid(row=0, column=0, sticky="ew", padx=(18, 10), pady=10)
        status_wrap.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            status_wrap,
            text="Loading media…",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.progress = ctk.CTkProgressBar(
            status_wrap,
            height=7,
            corner_radius=4,
            fg_color=SURFACE_3,
            progress_color=CYAN,
            mode="determinate",
        )
        self.progress.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.progress.set(0)

        self.preview_clip_button = ctk.CTkButton(
            bar,
            text="Preview edit (15s)",
            width=132,
            height=40,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            command=self.preview_edit_clip,
            state="disabled",
        )
        self.preview_clip_button.grid(row=0, column=1, padx=6, pady=10)

        self.cancel_button = ctk.CTkButton(
            bar,
            text="Cancel",
            width=82,
            height=40,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.cancel_export,
            state="disabled",
        )
        self.cancel_button.grid(row=0, column=2, padx=6, pady=10)

        self.open_export_button = ctk.CTkButton(
            bar,
            text="Open export",
            width=105,
            height=40,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            command=self.open_export,
            state="disabled",
        )
        self.open_export_button.grid(row=0, column=3, padx=6, pady=10)

        self.export_button = ctk.CTkButton(
            bar,
            text="Export edited media",
            width=155,
            height=40,
            fg_color=CYAN,
            hover_color=CYAN_HOVER,
            text_color="#031018",
            font=("Segoe UI Semibold", 11),
            command=self.export_media,
            state="disabled",
        )
        self.export_button.grid(row=0, column=4, padx=(6, 18), pady=10)

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
            self.schedule_preview(delay=10)
            self._start_thumbnail_generation()
        else:
            self.format_menu.configure(values=["MP3", "M4A", "WAV"])
            source_format = self.source_path.suffix.upper().lstrip(".")
            self.format_var.set(source_format if source_format in {"MP3", "M4A", "WAV"} else "MP3")
            self.tabs.set("Audio")
            self.crop_menu.configure(state="disabled")
            self.rotate_menu.configure(state="disabled")
            self.speed_menu.configure(state="normal")
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

    def _load_failed(self, error: str) -> None:
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
            if crop:
                x, y, width, height = crop
                image = image.crop((x, y, x + width, y + height))
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
            self.player = EmbeddedMediaPlayer(self.source_path)
            self.player.set_volume(0.0 if self.mute_var.get() else min(1.0, self.volume_var.get() / 100.0))
            self.player_rate_supported = self.player.set_rate(self._speed_value())
            self.player.seek(float(self.playhead_var.get()))
            self.play_button.configure(state="normal")
            self.preview_status_label.configure(text="Embedded player ready • Space = Play/Pause", text_color=MUTED)
            self._schedule_player_tick(20)
        except PlayerUnavailableError as exc:
            self.player = None
            self.play_button.configure(state="disabled")
            self.preview_status_label.configure(text="Embedded playback unavailable; using frame preview", text_color=WARNING)
            self.status_label.configure(text=str(exc), text_color=WARNING)
        except Exception as exc:
            self.player = None
            self.play_button.configure(state="disabled")
            self.preview_status_label.configure(text="Player failed; using frame preview", text_color=WARNING)
            self.status_label.configure(text=f"Player error: {exc}", text_color=WARNING)

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
            frame = self.player.next_frame()
            frame_pts: float | None = None
            if frame is not None:
                data, size, frame_pts, _schedule = frame
                if self.info and self.info.has_video:
                    image = Image.frombytes("RGB", size, data)
                    self.preview_pil = self._transform_live_frame(image)
                    self._draw_preview_image(self.preview_pil)

            position = self.player.position()
            if position is None:
                position = frame_pts

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

            self._schedule_player_tick(15 if self.playing else 90)
        except Exception as exc:
            self.pause_playback()
            self.preview_status_label.configure(text="Embedded playback error; frame preview still available", text_color=DANGER)
            self.status_label.configure(text=f"Playback error: {exc}", text_color=DANGER)
            if self.info and self.info.has_video:
                self.schedule_preview(delay=30)

    def toggle_playback(self) -> None:
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
        if self.player is None or not self.info:
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
            self.player.set_volume(0.0 if self.mute_var.get() else min(1.0, self.volume_var.get() / 100.0))
            self.player_rate_supported = self.player.set_rate(self._speed_value())
            self.player.play()
            self.playing = True
            self.play_button.configure(text="Ⅱ Pause", fg_color=CYAN, hover_color=CYAN_HOVER, text_color="#031018")
            rate_note = "" if self.player_rate_supported else " • speed applies on export"
            self.preview_status_label.configure(text=f"Playing selected range{rate_note}", text_color=SUCCESS)
            self._schedule_player_tick(5)
        except Exception as exc:
            self.playing = False
            self.status_label.configure(text=f"Could not start playback: {exc}", text_color=DANGER)

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
        if not self.info:
            return
        value = max(0.0, min(self.info.duration, float(value)))
        self.playhead_var.set(value)
        self.timeline.set_playhead(value)
        self._update_current_time_label()
        if self.player is not None:
            try:
                self.player.seek(value)
                self._schedule_player_tick(5)
                return
            except Exception:
                pass
        self.schedule_preview(delay=40)

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
        if not self.info or not self.info.has_video or self.player is not None:
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
        if generation != self.preview_generation:
            return
        self.preview_pil = image
        self._draw_preview_image(image)
        self.preview_status_label.configure(text="Preview ready", text_color=MUTED)

    def _preview_failed(self, error: str) -> None:
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
        count = 9
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
            except Exception:
                continue
        if generation == self.thumbnail_generation and images:
            self._post_ui(self.timeline.set_thumbnails, images)

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
        self.preview_status_label.configure(text="Audio waveform", text_color=MUTED)

    def _on_timeline_seek(self, value: float) -> None:
        self._seek_player(value)

    def _on_range_change(self, start: float, end: float) -> None:
        self.start_var.set(format_time(start))
        self.end_var.set(format_time(end))
        self._update_range_labels()
        self._seek_player(self.timeline.playhead)

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
            self.schedule_preview()
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

    def step_playhead(self, delta: float) -> None:
        if not self.info:
            return
        self._seek_player(self.playhead_var.get() + delta)

    def reset_range(self) -> None:
        if not self.info:
            return
        self.pause_playback()
        self.start_var.set("00:00.000")
        self.end_var.set(format_time(self.info.duration))
        self.timeline.set_media(self.info.duration, 0.0, self.info.duration, 0.0)
        self._update_range_labels()
        self._seek_player(0.0)

    def _crop_changed(self, value: str) -> None:
        custom = value == "Custom"
        for entry in self.custom_entries:
            entry.configure(state="normal" if custom else "disabled")
        self.apply_crop_button.configure(state="normal" if custom else "disabled")
        if custom and self.info:
            if not self.custom_w_var.get():
                self.custom_w_var.set(str(self.info.width))
            if not self.custom_h_var.get():
                self.custom_h_var.set(str(self.info.height))
        self.schedule_preview(delay=40)

    def _current_custom_crop(self) -> tuple[int, int, int, int] | None:
        if self.crop_var.get() != "Custom":
            return None
        return (
            int(self.custom_x_var.get() or 0),
            int(self.custom_y_var.get() or 0),
            int(self.custom_w_var.get() or 0),
            int(self.custom_h_var.get() or 0),
        )

    def apply_custom_crop(self) -> None:
        if not self.info:
            return
        try:
            crop = self._current_custom_crop()
            compute_crop(self.info, "Custom", crop)
            self.schedule_preview(delay=20)
            self.status_label.configure(text="Custom crop applied to preview", text_color=SUCCESS)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _speed_value(self) -> float:
        return float(self.speed_var.get().rstrip("x"))

    def _volume_changed(self, value: float) -> None:
        self.volume_text.configure(text=f"Volume {int(float(value))}%")
        if self.player is not None:
            self.player.set_volume(0.0 if self.mute_var.get() else min(1.0, float(value) / 100.0))

    def _sync_audio_state(self) -> None:
        if not self.info or not self.info.has_audio:
            return
        state = "disabled" if self.mute_var.get() else "normal"
        self.volume_slider.configure(state=state)
        self.fade_in_entry.configure(state=state)
        self.fade_out_entry.configure(state=state)
        if self.player is not None:
            self.player.set_volume(0.0 if self.mute_var.get() else min(1.0, self.volume_var.get() / 100.0))

    def _speed_changed(self, _value: str) -> None:
        if self.player is None:
            return
        supported = self.player.set_rate(self._speed_value())
        self.player_rate_supported = supported
        if supported:
            self.preview_status_label.configure(text=f"Playback speed: {self.speed_var.get()}", text_color=MUTED)
        else:
            self.preview_status_label.configure(text="Speed change will apply on export", text_color=WARNING)

    def reset_video_edits(self) -> None:
        self.crop_var.set("Original")
        self.rotate_var.set("0°")
        self.speed_var.set("1.0x")
        self._speed_changed("1.0x")
        for entry in self.custom_entries:
            entry.configure(state="disabled")
        self.apply_crop_button.configure(state="disabled")
        if self.info:
            self.custom_x_var.set("0")
            self.custom_y_var.set("0")
            self.custom_w_var.set(str(self.info.width))
            self.custom_h_var.set(str(self.info.height))
        self.schedule_preview(delay=40)

    def reset_audio_edits(self) -> None:
        self.mute_var.set(False)
        self.volume_var.set(100)
        self.speed_var.set("1.0x")
        self._speed_changed("1.0x")
        self.fade_in_var.set("0")
        self.fade_out_var.set("0")
        self._volume_changed(100)
        self._sync_audio_state()

    def reset_edits(self) -> None:
        self.reset_video_edits()
        self.reset_audio_edits()
        self.reset_range()
        self.status_label.configure(text="All edits reset", text_color=MUTED)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)

    def _validated_settings(self) -> dict[str, Any]:
        if not self.info:
            raise RuntimeError("Media is still loading.")

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
        if self.preview_clip_busy or self.export_process is not None:
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
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        self.preview_clip_busy = True
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
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.preview_clip_button.configure(state="normal", text="Preview edit (15s)")
        self.export_button.configure(state="normal")
        self.status_label.configure(text="Preview render failed", text_color=DANGER)
        messagebox.showerror(APP_TITLE, error, parent=self)

    def export_media(self) -> None:
        if self.export_process is not None or self.preview_clip_busy:
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
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        self.export_cancelled = False
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
        self.progress.set(0)
        self.export_button.configure(state="normal", text="Export edited media")
        self.preview_clip_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="Export cancelled", text_color=MUTED)

    def _export_done(self, output: Path) -> None:
        self.last_export = output
        self.progress.set(1)
        self.export_button.configure(state="normal", text="Export edited media")
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
        self.progress.set(0)
        self.export_button.configure(state="normal", text="Export edited media")
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
