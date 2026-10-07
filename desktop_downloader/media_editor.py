from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import customtkinter as ctk
from PIL import Image
from imageio_ffmpeg import get_ffmpeg_exe
from tkinter import filedialog, messagebox


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
CROP_PRESETS = {
    "Original": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "Custom": "custom",
}


def format_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def parse_time(value: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError("Time cannot be empty.")
    if ":" not in text:
        return max(0.0, float(text))
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("Use SS, MM:SS or HH:MM:SS.")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Invalid time value.") from exc
    if len(numbers) == 2:
        return max(0.0, numbers[0] * 60 + numbers[1])
    if len(numbers) == 3:
        return max(0.0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
    return max(0.0, numbers[0])


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _even(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def probe_media(path: Path) -> dict[str, Any]:
    command = [get_ffmpeg_exe(), "-hide_banner", "-i", str(path)]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    text = result.stderr or ""
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = 0.0
    if duration_match:
        duration = (
            int(duration_match.group(1)) * 3600
            + int(duration_match.group(2)) * 60
            + float(duration_match.group(3))
        )
    video_line = next((line for line in text.splitlines() if " Video: " in line), "")
    audio_line = next((line for line in text.splitlines() if " Audio: " in line), "")
    size_match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    width = int(size_match.group(1)) if size_match else 0
    height = int(size_match.group(2)) if size_match else 0
    has_video = bool(video_line)
    has_audio = bool(audio_line)
    if duration <= 0:
        raise RuntimeError("Could not read media duration.")
    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "width": width,
        "height": height,
    }


class MediaEditorWindow(ctk.CTkToplevel):
    def __init__(self, parent: Any, source_path: Path, output_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.source_path = Path(source_path)
        self.output_dir = Path(output_dir or self.source_path.parent)
        self.media: dict[str, Any] = {}
        self.preview_image: ctk.CTkImage | None = None
        self.last_export: Path | None = None
        self.exporting = False

        self.title("Media Editor")
        self.geometry("1120x820")
        self.minsize(980, 720)
        self.configure(fg_color=BG)

        self.start_var = ctk.StringVar(value="00:00.000")
        self.end_var = ctk.StringVar(value="")
        self.crop_var = ctk.StringVar(value="Original")
        self.rotate_var = ctk.StringVar(value="0°")
        self.speed_var = ctk.StringVar(value="1.0x")
        self.volume_var = ctk.DoubleVar(value=100.0)
        self.mute_var = ctk.BooleanVar(value=False)
        self.fade_in_var = ctk.StringVar(value="0")
        self.fade_out_var = ctk.StringVar(value="0")
        self.format_var = ctk.StringVar(value="MP4")
        self.quality_var = ctk.StringVar(value="High")
        self.output_name_var = ctk.StringVar(value=f"{self.source_path.stem}_edited")
        self.preview_time_var = ctk.DoubleVar(value=0.0)
        self.custom_x = ctk.StringVar(value="0")
        self.custom_y = ctk.StringVar(value="0")
        self.custom_w = ctk.StringVar(value="")
        self.custom_h = ctk.StringVar(value="")

        self._build_ui()
        self.after(80, self._load_media)

    def _card(self, master: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(master, fg_color=SURFACE, corner_radius=16, border_width=1, border_color=BORDER)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, height=62, corner_radius=0, fg_color=SURFACE)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="ME", width=36, height=36, corner_radius=10, fg_color=PURPLE, text_color=TEXT,
                     font=("Segoe UI Semibold", 13)).grid(row=0, column=0, padx=(22, 12), pady=13)
        title = ctk.CTkFrame(top, fg_color="transparent")
        title.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(title, text="MEDIA EDITOR", text_color=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
        self.source_label = ctk.CTkLabel(title, text=self.source_path.name, text_color=MUTED, font=("Segoe UI", 9))
        self.source_label.pack(anchor="w")
        ctk.CTkButton(top, text="Open source", width=110, height=34, fg_color=SURFACE_2, hover_color=SURFACE_3,
                      border_width=1, border_color=BORDER, command=self.open_source).grid(row=0, column=2, padx=(10, 22))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=SURFACE_3,
                                      scrollbar_button_hover_color=PURPLE)
        body.grid(row=1, column=0, sticky="nsew", padx=22, pady=(14, 20))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)

        preview_card = self._card(body)
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        preview_card.grid_columnconfigure(0, weight=1)
        self.preview_label = ctk.CTkLabel(
            preview_card,
            text="Loading preview…",
            height=350,
            fg_color="#040810",
            text_color=MUTED,
            font=("Segoe UI", 12),
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)

        self.preview_slider = ctk.CTkSlider(
            preview_card,
            from_=0,
            to=1,
            number_of_steps=1000,
            variable=self.preview_time_var,
            command=self._preview_position_changed,
            progress_color=CYAN,
            button_color=TEXT,
            button_hover_color=CYAN,
            state="disabled",
        )
        self.preview_slider.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        preview_actions = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_actions.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        preview_actions.grid_columnconfigure(0, weight=1)
        self.preview_time_label = ctk.CTkLabel(preview_actions, text="00:00.000", text_color=MUTED, font=("Segoe UI", 10))
        self.preview_time_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(preview_actions, text="Set start here", width=105, height=34, fg_color=SURFACE_2,
                      hover_color=SURFACE_3, command=self.set_start_from_preview).grid(row=0, column=1, padx=4)
        ctk.CTkButton(preview_actions, text="Set end here", width=100, height=34, fg_color=SURFACE_2,
                      hover_color=SURFACE_3, command=self.set_end_from_preview).grid(row=0, column=2, padx=4)
        self.refresh_button = ctk.CTkButton(preview_actions, text="Refresh frame", width=110, height=34, fg_color=PURPLE,
                                            hover_color=PURPLE_HOVER, command=self.refresh_preview)
        self.refresh_button.grid(row=0, column=3, padx=(4, 0))

        trim_card = self._card(body)
        trim_card.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 12))
        trim_card.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(trim_card, text="TRIM / CUT", text_color=CYAN, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8)
        )
        ctk.CTkLabel(trim_card, text="Start", text_color=MUTED).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkLabel(trim_card, text="End", text_color=MUTED).grid(row=1, column=1, sticky="w", padx=16)
        ctk.CTkEntry(trim_card, textvariable=self.start_var, height=38).grid(row=2, column=0, sticky="ew", padx=(16, 8), pady=(5, 15))
        ctk.CTkEntry(trim_card, textvariable=self.end_var, height=38).grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=(5, 15))

        video_card = self._card(body)
        video_card.grid(row=2, column=0, sticky="ew", padx=(0, 8), pady=(0, 12))
        video_card.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(video_card, text="VIDEO CONTROLS", text_color=CYAN, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 8)
        )
        ctk.CTkLabel(video_card, text="Crop / Aspect", text_color=MUTED).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkLabel(video_card, text="Rotate", text_color=MUTED).grid(row=1, column=1, sticky="w", padx=8)
        ctk.CTkLabel(video_card, text="Speed", text_color=MUTED).grid(row=1, column=2, sticky="w", padx=8)
        self.crop_menu = ctk.CTkOptionMenu(video_card, variable=self.crop_var, values=list(CROP_PRESETS),
                                           command=self._crop_changed, fg_color=SURFACE_3, button_color=PURPLE)
        self.crop_menu.grid(row=2, column=0, sticky="ew", padx=(16, 8), pady=(5, 10))
        self.rotate_menu = ctk.CTkOptionMenu(video_card, variable=self.rotate_var, values=["0°", "90°", "180°", "270°"],
                                             command=lambda _: self.refresh_preview(), fg_color=SURFACE_3, button_color=PURPLE)
        self.rotate_menu.grid(row=2, column=1, sticky="ew", padx=8, pady=(5, 10))
        self.speed_menu = ctk.CTkOptionMenu(video_card, variable=self.speed_var,
                                            values=["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
                                            fg_color=SURFACE_3, button_color=PURPLE)
        self.speed_menu.grid(row=2, column=2, sticky="ew", padx=(8, 16), pady=(5, 10))

        custom = ctk.CTkFrame(video_card, fg_color="transparent")
        custom.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 10))
        for col in range(4):
            custom.grid_columnconfigure(col, weight=1)
        self.custom_entries: list[ctk.CTkEntry] = []
        for col, (label, variable) in enumerate([
            ("X", self.custom_x), ("Y", self.custom_y), ("Width", self.custom_w), ("Height", self.custom_h)
        ]):
            wrap = ctk.CTkFrame(custom, fg_color="transparent")
            wrap.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 5, 0))
            ctk.CTkLabel(wrap, text=label, text_color=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            entry = ctk.CTkEntry(wrap, textvariable=variable, height=34)
            entry.pack(fill="x", pady=(3, 0))
            self.custom_entries.append(entry)

        audio = ctk.CTkFrame(video_card, fg_color="transparent")
        audio.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 14))
        audio.grid_columnconfigure(1, weight=1)
        self.mute_switch = ctk.CTkSwitch(audio, text="Mute audio", variable=self.mute_var, text_color=TEXT,
                                         progress_color=PURPLE, command=self._sync_audio_controls)
        self.mute_switch.grid(row=0, column=0, sticky="w")
        self.volume_slider = ctk.CTkSlider(audio, from_=0, to=200, variable=self.volume_var, number_of_steps=200,
                                           progress_color=CYAN, button_color=TEXT)
        self.volume_slider.grid(row=0, column=1, sticky="ew", padx=(16, 8))
        self.volume_label = ctk.CTkLabel(audio, text="Volume 100%", width=95, text_color=MUTED)
        self.volume_label.grid(row=0, column=2, sticky="e")
        self.volume_slider.configure(command=self._volume_changed)

        side = ctk.CTkFrame(body, fg_color="transparent")
        side.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(8, 0))
        side.grid_columnconfigure(0, weight=1)

        fade_card = self._card(side)
        fade_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        fade_card.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(fade_card, text="AUDIO FADE", text_color=CYAN, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8)
        )
        ctk.CTkLabel(fade_card, text="Fade in (sec)", text_color=MUTED).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkLabel(fade_card, text="Fade out (sec)", text_color=MUTED).grid(row=1, column=1, sticky="w", padx=8)
        self.fade_in_entry = ctk.CTkEntry(fade_card, textvariable=self.fade_in_var, height=36)
        self.fade_in_entry.grid(row=2, column=0, sticky="ew", padx=(16, 8), pady=(5, 14))
        self.fade_out_entry = ctk.CTkEntry(fade_card, textvariable=self.fade_out_var, height=36)
        self.fade_out_entry.grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=(5, 14))

        export_card = self._card(side)
        export_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        export_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(export_card, text="EXPORT", text_color=CYAN, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )
        ctk.CTkLabel(export_card, text="File name", text_color=MUTED).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkEntry(export_card, textvariable=self.output_name_var, height=38).grid(
            row=2, column=0, sticky="ew", padx=16, pady=(5, 10)
        )
        row = ctk.CTkFrame(export_card, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=16)
        row.grid_columnconfigure((0, 1), weight=1)
        format_wrap = ctk.CTkFrame(row, fg_color="transparent")
        format_wrap.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkLabel(format_wrap, text="Format", text_color=MUTED).pack(anchor="w")
        self.format_menu = ctk.CTkOptionMenu(format_wrap, variable=self.format_var, values=["MP4", "MKV", "MOV"],
                                              fg_color=SURFACE_3, button_color=PURPLE)
        self.format_menu.pack(fill="x", pady=(4, 0))
        quality_wrap = ctk.CTkFrame(row, fg_color="transparent")
        quality_wrap.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ctk.CTkLabel(quality_wrap, text="Quality", text_color=MUTED).pack(anchor="w")
        self.quality_menu = ctk.CTkOptionMenu(quality_wrap, variable=self.quality_var,
                                               values=["High", "Medium", "Small"],
                                               fg_color=SURFACE_3, button_color=PURPLE)
        self.quality_menu.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(export_card, text="Choose output folder", height=36, fg_color=SURFACE_2,
                      hover_color=SURFACE_3, command=self.choose_output_folder).grid(
            row=4, column=0, sticky="ew", padx=16, pady=(12, 6)
        )
        self.output_dir_label = ctk.CTkLabel(export_card, text=str(self.output_dir), text_color=MUTED,
                                             font=("Segoe UI", 9), wraplength=330, justify="left")
        self.output_dir_label.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 12))

        action_card = self._card(side)
        action_card.grid(row=2, column=0, sticky="ew")
        action_card.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(action_card, text="Loading media…", text_color=MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self.progress = ctk.CTkProgressBar(action_card, height=8, fg_color=SURFACE_3, progress_color=CYAN)
        self.progress.grid(row=1, column=0, sticky="ew", padx=16)
        self.progress.set(0)
        self.export_button = ctk.CTkButton(action_card, text="Export edited media", height=48, fg_color=CYAN,
                                           hover_color=CYAN_HOVER, text_color="#031018",
                                           font=("Segoe UI Semibold", 12), command=self.export_media, state="disabled")
        self.export_button.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 7))
        self.open_export_button = ctk.CTkButton(action_card, text="Open exported file", height=38, fg_color=SURFACE_2,
                                                hover_color=SURFACE_3, command=self.open_export, state="disabled")
        self.open_export_button.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))

        self._crop_changed("Original")

    def _load_media(self) -> None:
        try:
            if not self.source_path.exists():
                raise FileNotFoundError("Source file no longer exists.")
            self.media = probe_media(self.source_path)
            duration = float(self.media["duration"])
            self.end_var.set(format_time(duration))
            self.preview_slider.configure(to=max(duration, 0.001), state="normal")
            self.preview_time_var.set(min(duration / 2, 2.0))
            self.custom_w.set(str(self.media.get("width") or ""))
            self.custom_h.set(str(self.media.get("height") or ""))
            kind = "Video" if self.media["has_video"] else "Audio"
            details = f"{kind} • {format_time(duration)}"
            if self.media["has_video"] and self.media["width"] and self.media["height"]:
                details += f" • {self.media['width']}×{self.media['height']}"
            self.source_label.configure(text=f"{self.source_path.name}  •  {details}")
            if not self.media["has_video"]:
                self.crop_menu.configure(state="disabled")
                self.rotate_menu.configure(state="disabled")
                self.preview_slider.configure(state="disabled")
                self.refresh_button.configure(state="disabled")
                self.preview_label.configure(text="Audio file\nUse trim, speed, volume and fade controls.", image=None)
                self.format_menu.configure(values=["MP3", "M4A", "WAV"])
                suffix = self.source_path.suffix.upper().lstrip(".")
                self.format_var.set(suffix if suffix in {"MP3", "M4A", "WAV"} else "MP3")
            else:
                self.format_menu.configure(values=["MP4", "MKV", "MOV"])
                suffix = self.source_path.suffix.upper().lstrip(".")
                self.format_var.set(suffix if suffix in {"MP4", "MKV", "MOV"} else "MP4")
                self.refresh_preview()
            if not self.media["has_audio"]:
                self.mute_switch.configure(state="disabled")
                self.volume_slider.configure(state="disabled")
                self.fade_in_entry.configure(state="disabled")
                self.fade_out_entry.configure(state="disabled")
            self.status_label.configure(text="Ready to edit", text_color=SUCCESS)
            self.export_button.configure(state="normal")
        except Exception as exc:
            self.status_label.configure(text="Could not load media", text_color=DANGER)
            messagebox.showerror("Media Editor", str(exc), parent=self)

    def _preview_position_changed(self, value: float) -> None:
        self.preview_time_label.configure(text=format_time(float(value)))

    def set_start_from_preview(self) -> None:
        self.start_var.set(format_time(float(self.preview_time_var.get())))

    def set_end_from_preview(self) -> None:
        self.end_var.set(format_time(float(self.preview_time_var.get())))

    def _crop_changed(self, value: str) -> None:
        custom = value == "Custom"
        for entry in getattr(self, "custom_entries", []):
            entry.configure(state="normal" if custom else "disabled")
        if self.media.get("has_video"):
            self.refresh_preview()

    def _volume_changed(self, value: float) -> None:
        self.volume_label.configure(text=f"Volume {int(float(value))}%")

    def _sync_audio_controls(self) -> None:
        state = "disabled" if self.mute_var.get() else "normal"
        if self.media.get("has_audio", True):
            self.volume_slider.configure(state=state)
            self.fade_in_entry.configure(state=state)
            self.fade_out_entry.configure(state=state)

    def _crop_filter(self) -> str | None:
        if not self.media.get("has_video"):
            return None
        preset = self.crop_var.get()
        if preset == "Original":
            return None
        source_w = int(self.media.get("width") or 0)
        source_h = int(self.media.get("height") or 0)
        if source_w <= 0 or source_h <= 0:
            raise ValueError("Video dimensions could not be detected.")
        if preset == "Custom":
            x = max(0, int(self.custom_x.get() or 0))
            y = max(0, int(self.custom_y.get() or 0))
            width = _even(int(self.custom_w.get()))
            height = _even(int(self.custom_h.get()))
            if x + width > source_w or y + height > source_h:
                raise ValueError("Custom crop is outside the source frame.")
            return f"crop={width}:{height}:{x}:{y}"
        ratio = CROP_PRESETS[preset]
        if not isinstance(ratio, tuple):
            return None
        target = ratio[0] / ratio[1]
        source = source_w / source_h
        if source > target:
            height = _even(source_h)
            width = _even(int(height * target))
        else:
            width = _even(source_w)
            height = _even(int(width / target))
        x = max(0, (source_w - width) // 2)
        y = max(0, (source_h - height) // 2)
        return f"crop={width}:{height}:{x}:{y}"

    def _video_filters(self, include_speed: bool = True) -> list[str]:
        filters: list[str] = []
        crop = self._crop_filter()
        if crop:
            filters.append(crop)
        rotate = self.rotate_var.get()
        if rotate == "90°":
            filters.append("transpose=1")
        elif rotate == "180°":
            filters.extend(["hflip", "vflip"])
        elif rotate == "270°":
            filters.append("transpose=2")
        speed = float(self.speed_var.get().rstrip("x"))
        if include_speed and abs(speed - 1.0) > 0.001:
            filters.append(f"setpts=PTS/{speed:g}")
        return filters

    def _audio_filters(self, output_duration: float) -> list[str]:
        filters: list[str] = []
        speed = float(self.speed_var.get().rstrip("x"))
        if abs(speed - 1.0) > 0.001:
            filters.append(f"atempo={speed:g}")
        volume = float(self.volume_var.get()) / 100.0
        if abs(volume - 1.0) > 0.001:
            filters.append(f"volume={volume:.3f}")
        fade_in = max(0.0, float(self.fade_in_var.get() or 0))
        fade_out = max(0.0, float(self.fade_out_var.get() or 0))
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={min(fade_in, output_duration):.3f}")
        if fade_out > 0:
            start = max(0.0, output_duration - fade_out)
            filters.append(f"afade=t=out:st={start:.3f}:d={min(fade_out, output_duration):.3f}")
        return filters

    def refresh_preview(self) -> None:
        if not self.media.get("has_video") or not self.source_path.exists():
            return
        position = float(self.preview_time_var.get())
        self.preview_label.configure(text="Rendering frame…", image=None)
        threading.Thread(target=self._preview_worker, args=(position,), daemon=True).start()

    def _preview_worker(self, position: float) -> None:
        try:
            filters = self._video_filters(include_speed=False)
            filters.append("scale=760:430:force_original_aspect_ratio=decrease")
            command = [
                get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-ss", f"{position:.3f}", "-i", str(self.source_path),
                "-frames:v", "1", "-vf", ",".join(filters),
                "-f", "image2pipe", "-vcodec", "png", "pipe:1",
            ]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
            if result.returncode != 0 or not result.stdout:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-800:] or "Preview failed.")
            data = result.stdout
            self.after(0, lambda: self._apply_preview(data))
        except Exception as exc:
            self.after(0, lambda: self.preview_label.configure(text=f"Preview unavailable\n{exc}", image=None))

    def _apply_preview(self, data: bytes) -> None:
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            self.preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            self.preview_label.configure(image=self.preview_image, text="")
        except Exception as exc:
            self.preview_label.configure(text=f"Preview unavailable\n{exc}", image=None)

    def _validate_range(self) -> tuple[float, float, float]:
        start = parse_time(self.start_var.get())
        end = parse_time(self.end_var.get())
        duration = float(self.media["duration"])
        if start >= duration:
            raise ValueError("Start time must be before the end of the media.")
        end = min(end, duration)
        if end <= start:
            raise ValueError("End time must be after start time.")
        return start, end, end - start

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.output_dir), parent=self)
        if selected:
            self.output_dir = Path(selected)
            self.output_dir_label.configure(text=str(self.output_dir))

    def open_source(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(self.source_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.source_path)])
            else:
                subprocess.Popen(["xdg-open", str(self.source_path)])
        except Exception as exc:
            messagebox.showerror("Media Editor", str(exc), parent=self)

    def _build_output_path(self) -> Path:
        name = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1f]+', "_", self.output_name_var.get().strip()).strip(" .")
        if not name:
            name = f"{self.source_path.stem}_edited"
        extension = "." + self.format_var.get().lower()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.output_dir / f"{name}{extension}"
        if candidate.resolve() == self.source_path.resolve():
            candidate = self.output_dir / f"{name}_edited{extension}"
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            alternate = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
            if not alternate.exists():
                return alternate
            index += 1

    def export_media(self) -> None:
        if self.exporting:
            return
        try:
            start, end, clip_duration = self._validate_range()
            output = self._build_output_path()
            speed = float(self.speed_var.get().rstrip("x"))
            output_duration = clip_duration / speed
            video_filters = self._video_filters() if self.media.get("has_video") else []
            audio_filters = []
            if self.media.get("has_audio") and not self.mute_var.get():
                audio_filters = self._audio_filters(output_duration)
        except Exception as exc:
            messagebox.showerror("Media Editor", str(exc), parent=self)
            return

        self.exporting = True
        self.export_button.configure(state="disabled")
        self.open_export_button.configure(state="disabled")
        self.status_label.configure(text="Exporting edited media…", text_color=WARNING)
        self.progress.start()
        threading.Thread(
            target=self._export_worker,
            args=(start, clip_duration, output, video_filters, audio_filters),
            daemon=True,
        ).start()

    def _export_worker(
        self,
        start: float,
        clip_duration: float,
        output: Path,
        video_filters: list[str],
        audio_filters: list[str],
    ) -> None:
        try:
            command = [
                get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start:.3f}", "-i", str(self.source_path), "-t", f"{clip_duration:.3f}",
            ]
            if self.media.get("has_video"):
                command += ["-map", "0:v:0"]
                if video_filters:
                    command += ["-vf", ",".join(video_filters)]
                quality = self.quality_var.get()
                crf = {"High": "18", "Medium": "23", "Small": "28"}.get(quality, "18")
                command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]
                if self.media.get("has_audio") and not self.mute_var.get():
                    command += ["-map", "0:a:0?"]
                    if audio_filters:
                        command += ["-af", ",".join(audio_filters)]
                    command += ["-c:a", "aac", "-b:a", "192k"]
                else:
                    command += ["-an"]
                if output.suffix.lower() in {".mp4", ".mov"}:
                    command += ["-movflags", "+faststart"]
            else:
                if audio_filters:
                    command += ["-af", ",".join(audio_filters)]
                suffix = output.suffix.lower()
                if suffix == ".mp3":
                    command += ["-c:a", "libmp3lame", "-b:a", "192k"]
                elif suffix == ".m4a":
                    command += ["-c:a", "aac", "-b:a", "192k"]
                elif suffix == ".wav":
                    command += ["-c:a", "pcm_s16le"]
            command.append(str(output))
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "FFmpeg export failed.")[-1800:])
            self.after(0, lambda: self._export_done(output))
        except Exception as exc:
            self.after(0, lambda: self._export_failed(str(exc)))

    def _export_done(self, output: Path) -> None:
        self.exporting = False
        self.progress.stop()
        self.progress.set(1)
        self.last_export = output
        self.status_label.configure(text=f"Export complete • {output.name}", text_color=SUCCESS)
        self.export_button.configure(state="normal")
        self.open_export_button.configure(state="normal")
        messagebox.showinfo("Media Editor", f"Export completed successfully.\n\n{output}", parent=self)

    def _export_failed(self, error: str) -> None:
        self.exporting = False
        self.progress.stop()
        self.progress.set(0)
        self.status_label.configure(text="Export failed", text_color=DANGER)
        self.export_button.configure(state="normal")
        messagebox.showerror("Media Editor", error, parent=self)

    def open_export(self) -> None:
        if self.last_export and self.last_export.exists():
            try:
                if os.name == "nt":
                    os.startfile(str(self.last_export))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(self.last_export)])
                else:
                    subprocess.Popen(["xdg-open", str(self.last_export)])
            except Exception as exc:
                messagebox.showerror("Media Editor", str(exc), parent=self)
