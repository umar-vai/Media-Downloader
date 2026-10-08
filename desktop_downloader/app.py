from __future__ import annotations

import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import customtkinter as ctk
import yt_dlp
from PIL import Image
from imageio_ffmpeg import get_ffmpeg_exe
from tkinter import filedialog, messagebox

from app_logging import get_logger, log_path
from browser_capture import BrowserCaptureBridge, CaptureStore, DEFAULT_CAPTURE_PORT, generate_capture_token
from browser_capture_window import BrowserCaptureWindow
from captured_media_engine import CaptureDownloadCancelled, capture_host, capture_media_mode, download_captured_media
from diagnostics_window import DiagnosticsWindow
from history_store import HistoryStore, make_history_entry
from history_window import HistoryWindow
from install_mode import is_installed_mode
from media_editor import MediaEditorWindow
from media_sources import browser_headers, detect_platform, extraction_attempts, is_supported_media_url, platform_name, request_options, video_format_selector
from settings_window import SettingsWindow
from update_manager import LATEST_RELEASE_WEB, ReleaseInfo, download_installer_release, download_release, fetch_latest_release, is_newer_version
from version import APP_VERSION

APP_NAME = "Media Downloader"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Media Downloader"
CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
CONFIG_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
LEGACY_CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / ("Team" + "Fahad" + "Downloader")
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "settings.json"
UPDATE_DIR = Path(os.getenv("LOCALAPPDATA") or CONFIG_DIR) / "MediaDownloader" / "updates"
UPDATE_RESULT_FILE = CONFIG_DIR / "update-result.json"
UPDATE_LOG_FILE = CONFIG_DIR / "update.log"

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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LOGGER = get_logger("main")


class TaskState(str, Enum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    READY = "ready"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    CANCELLED = "cancelled"
    ERROR = "error"


def safe_filename(value: str, fallback: str = "media_download") -> str:
    text = (value or "").strip()
    text = re.sub(r"\.(mp3|m4a|mp4|webm|mkv|mov)$", "", text, flags=re.I)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:140] or fallback).strip()


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return "--:--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def default_settings() -> dict[str, Any]:
    return {
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "auto_check_updates": True,
        "auto_download_updates": False,
        "auto_analyze_links": True,
        "open_editor_after_download": False,
        "confirm_before_exit": True,
        "default_mode": "Video",
        "video_quality": "720p",
        "audio_format": "MP3",
        "audio_quality": "192",
        "browser_capture_token": "",
        "browser_capture_port": DEFAULT_CAPTURE_PORT,
        "snooze_version": "",
        "snooze_until": 0,
    }


def normalize_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    settings = default_settings()
    if isinstance(payload, dict):
        settings.update(payload)

    mode = str(settings.get("default_mode") or "Video")
    settings["default_mode"] = mode if mode in {"Video", "Audio"} else "Video"

    video_quality = str(settings.get("video_quality") or "720p")
    settings["video_quality"] = (
        video_quality
        if video_quality in {"Best available", "1080p", "720p", "480p", "360p"}
        else "720p"
    )

    audio_format = str(settings.get("audio_format") or "MP3").upper()
    settings["audio_format"] = audio_format if audio_format in {"MP3", "M4A"} else "MP3"

    audio_quality = str(settings.get("audio_quality") or "192")
    settings["audio_quality"] = audio_quality if audio_quality in {"320", "256", "192", "128"} else "192"

    download_dir = str(settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR).strip()
    settings["download_dir"] = download_dir or str(DEFAULT_DOWNLOAD_DIR)

    for key, fallback in (
        ("auto_check_updates", True),
        ("auto_download_updates", False),
        ("auto_analyze_links", True),
        ("open_editor_after_download", False),
        ("confirm_before_exit", True),
    ):
        value = settings.get(key, fallback)
        settings[key] = value if isinstance(value, bool) else fallback

    settings["browser_capture_token"] = str(settings.get("browser_capture_token") or "").strip()
    try:
        capture_port = int(settings.get("browser_capture_port") or DEFAULT_CAPTURE_PORT)
    except (TypeError, ValueError):
        capture_port = DEFAULT_CAPTURE_PORT
    settings["browser_capture_port"] = capture_port if 1 <= capture_port <= 65535 else DEFAULT_CAPTURE_PORT

    settings["snooze_version"] = str(settings.get("snooze_version") or "")
    try:
        settings["snooze_until"] = float(settings.get("snooze_until") or 0)
    except (TypeError, ValueError):
        settings["snooze_until"] = 0

    return settings


def load_settings() -> dict[str, Any]:
    source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
    payload: dict[str, Any] | None = None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            payload = raw
    except Exception:
        payload = None

    settings = normalize_settings(payload)
    if source == LEGACY_CONFIG_FILE and source.exists() and not CONFIG_FILE.exists():
        save_settings(settings)
    return settings


def save_settings(settings: dict[str, Any]) -> bool:
    temp_file = CONFIG_FILE.with_suffix(".json.tmp")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(CONFIG_FILE)
        return True
    except Exception:
        LOGGER.exception("Could not save settings")
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def human_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


def is_frozen_windows_app() -> bool:
    return bool(os.name == "nt" and getattr(sys, "frozen", False))


def append_update_log(message: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with UPDATE_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def get_bundled_updater_path() -> Path | None:
    if not is_frozen_windows_app():
        return None
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidate = bundle_dir / "MediaDownloaderUpdater.exe"
    return candidate if candidate.exists() else None


class DownloaderApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1080x720")
        self.minsize(860, 620)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._close_app)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_info: dict[str, Any] | None = None
        self.thumbnail_image: ctk.CTkImage | None = None
        self.last_file: Path | None = None
        self.is_busy = False
        self.edit_after_download = False
        self.editor_window: MediaEditorWindow | None = None
        self.diagnostics_window: DiagnosticsWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.history_window: HistoryWindow | None = None
        self.browser_capture_window: BrowserCaptureWindow | None = None
        self.task_state = TaskState.IDLE
        self._job_counter = 0
        self.active_job_id: int | None = None
        self.active_job_cancel: threading.Event | None = None

        self.settings = load_settings()
        if not str(self.settings.get("browser_capture_token") or "").strip():
            self.settings["browser_capture_token"] = generate_capture_token()
            save_settings(self.settings)
        self.capture_store = CaptureStore()
        self.capture_bridge: BrowserCaptureBridge | None = None
        self.history_store = HistoryStore(HISTORY_FILE)
        self.download_dir = Path(str(self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR)).expanduser()
        self.latest_release: ReleaseInfo | None = None
        self.downloaded_update: Path | None = None
        self.update_checking = False
        self.update_downloading = False
        self.update_cancel_event: threading.Event | None = None

        self.url_var = ctk.StringVar()
        self.name_var = ctk.StringVar()
        self.open_editor_after_var = ctk.BooleanVar(value=bool(self.settings.get("open_editor_after_download", False)))
        self.url_auto_after_id: str | None = None
        self.last_analyzed_url = ""
        self.update_expanded = False
        self.mode_var = ctk.StringVar(value=str(self.settings.get("default_mode") or "Video"))
        self.video_quality_var = ctk.StringVar(value=str(self.settings.get("video_quality") or "720p"))
        self.audio_format_var = ctk.StringVar(value=str(self.settings.get("audio_format") or "MP3"))
        self.audio_quality_var = ctk.StringVar(value=str(self.settings.get("audio_quality") or "192"))
        self.download_dir_var = ctk.StringVar(value=str(self.download_dir))
        self.auto_check_updates_var = ctk.BooleanVar(value=bool(self.settings.get("auto_check_updates", True)))
        self.auto_download_updates_var = ctk.BooleanVar(value=bool(self.settings.get("auto_download_updates", False)))

        self._center_window()
        self._build_ui()
        self._sync_recent_file()
        self._start_browser_capture_bridge()
        LOGGER.info("App started version=%s executable=%s", APP_VERSION, sys.executable)
        self.after(120, self._drain_events)
        self.after(700, lambda: self._show_update_result(attempt=0))
        if self.auto_check_updates_var.get():
            self.after(1500, lambda: self.check_for_updates(manual=False))

    def _close_app(self) -> None:
        active = self.is_busy or self.update_downloading
        if active:
            should_confirm = bool(self.settings.get("confirm_before_exit", True))
            if should_confirm and not messagebox.askyesno(
                APP_NAME,
                "A task is still running. Cancel it and close Media Downloader?",
                parent=self,
            ):
                return
            if self.active_job_cancel is not None:
                self.active_job_cancel.set()
            if self.update_cancel_event is not None:
                self.update_cancel_event.set()
        if self.capture_bridge is not None:
            try:
                self.capture_bridge.stop()
            except Exception:
                LOGGER.exception("Could not stop browser capture bridge")
        LOGGER.info(
            "App closing task_state=%s update_downloading=%s",
            self.task_state.value,
            self.update_downloading,
        )
        self.destroy()

    def _center_window(self) -> None:
        self.update_idletasks()
        width, height = 1120, 760
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_topbar()
        self.content = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=SURFACE_3,
            scrollbar_button_hover_color=PURPLE,
        )
        self.content.grid(row=1, column=0, sticky="nsew", padx=24, pady=(8, 22))
        self.content.grid_columnconfigure(0, weight=1)
        self._build_hero()
        self._build_url_card()
        self._build_media_card()
        self._build_settings_card()
        self._build_download_card()
        self._build_update_card()
        self._build_footer()
        self.url_entry.focus_set()
        self._sync_mode(self.mode_var.get())

    def _build_topbar(self) -> None:
        top = ctk.CTkFrame(self, height=62, corner_radius=0, fg_color=SURFACE, border_width=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top,
            text="MD",
            width=36,
            height=36,
            corner_radius=10,
            fg_color=PURPLE,
            text_color=TEXT,
            font=("Segoe UI Semibold", 13),
        ).grid(row=0, column=0, padx=(24, 12), pady=13)

        brand = ctk.CTkFrame(top, fg_color="transparent")
        brand.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(brand, text="MEDIA DOWNLOADER", text_color=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
        ctk.CTkLabel(brand, text="YOUTUBE • FACEBOOK • INSTAGRAM", text_color=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        self.top_update_button = ctk.CTkButton(
            top,
            text="UPDATE AVAILABLE",
            width=130,
            height=30,
            corner_radius=9,
            fg_color="#342A12",
            hover_color="#493A17",
            text_color=WARNING,
            font=("Segoe UI Semibold", 9),
            command=self.download_or_install_update,
        )
        self.top_update_button.grid(row=0, column=2, padx=(12, 0))
        self.top_update_button.grid_remove()

        self.browser_capture_button = ctk.CTkButton(
            top,
            text="Capture",
            width=82,
            height=30,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=("Segoe UI Semibold", 9),
            command=self.open_browser_capture,
        )
        self.browser_capture_button.grid(row=0, column=3, padx=(12, 0))

        ctk.CTkButton(
            top,
            text="History",
            width=82,
            height=30,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=("Segoe UI Semibold", 9),
            command=self.open_history,
        ).grid(row=0, column=4, padx=(8, 0))

        ctk.CTkButton(
            top,
            text="Settings",
            width=88,
            height=30,
            corner_radius=9,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=("Segoe UI Semibold", 9),
            command=self.open_settings,
        ).grid(row=0, column=5, padx=(8, 0))

        ctk.CTkLabel(
            top,
            text=f"DESKTOP  •  v{APP_VERSION}",
            height=28,
            corner_radius=9,
            fg_color=SURFACE_2,
            text_color=CYAN,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=6, padx=(12, 24))

    def _build_hero(self) -> None:
        hero = ctk.CTkFrame(self.content, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="ew", pady=(18, 16))
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hero, text="Media Downloader", text_color=TEXT, font=("Segoe UI Semibold", 31)).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            hero,
            text="Download public videos and audio from YouTube, Facebook and Instagram.",
            text_color=MUTED,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ctk.CTkLabel(
            hero,
            text="LOCAL ENGINE  •  READY",
            height=30,
            corner_radius=10,
            fg_color="#0D2A2A",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(20, 10))

        self.edit_local_button = ctk.CTkButton(
            hero,
            text="Edit local media",
            width=132,
            height=34,
            corner_radius=10,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self.open_editor,
        )
        self.edit_local_button.grid(row=0, column=2, rowspan=2, sticky="e")

    def _card(self, master: Any, **kwargs: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(master, fg_color=SURFACE, corner_radius=18, border_width=1, border_color=BORDER, **kwargs)

    def _section_title(
        self,
        master: Any,
        kicker: str,
        title: str,
        row: int = 0,
        column: int = 0,
        columnspan: int = 1,
    ) -> None:
        wrap = ctk.CTkFrame(master, fg_color="transparent")
        wrap.grid(row=row, column=column, columnspan=columnspan, sticky="w")
        ctk.CTkLabel(wrap, text=kicker.upper(), text_color=CYAN, font=("Segoe UI Semibold", 9)).pack(anchor="w")
        ctk.CTkLabel(wrap, text=title, text_color=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w", pady=(2, 0))

    def _build_url_card(self) -> None:
        card = self._card(self.content)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        self._section_title(card, "01 / Source", "Paste a media link")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=18, pady=(16, 18))
        row.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            row,
            textvariable=self.url_var,
            height=48,
            corner_radius=12,
            fg_color=SURFACE_2,
            border_color="#2A4169",
            border_width=1,
            text_color=TEXT,
            placeholder_text="YouTube, Facebook or Instagram URL",
            placeholder_text_color="#667995",
            font=("Segoe UI", 11),
        )
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda _event: self.analyze_media())
        self.url_entry.bind("<KeyRelease>", self._on_url_changed)

        self.paste_button = ctk.CTkButton(
            row,
            text="Paste",
            width=82,
            height=48,
            corner_radius=12,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            border_width=1,
            border_color="#2A4169",
            command=self.paste_from_clipboard,
        )
        self.paste_button.grid(row=0, column=1, padx=(10, 0))

        self.read_button = ctk.CTkButton(
            row,
            text="Analyze media",
            width=132,
            height=48,
            corner_radius=12,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            text_color="#FFFFFF",
            font=("Segoe UI Semibold", 11),
            command=self.analyze_media,
        )
        self.read_button.grid(row=0, column=2, padx=(10, 0))

    def _build_media_card(self) -> None:
        self.media_card = self._card(self.content)
        self.media_card.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.media_card.grid_columnconfigure(1, weight=1)

        self.thumbnail_frame = ctk.CTkFrame(
            self.media_card,
            width=214,
            height=122,
            corner_radius=14,
            fg_color=SURFACE_2,
            border_width=1,
            border_color="#24395C",
        )
        self.thumbnail_frame.grid(row=0, column=0, rowspan=3, sticky="nw", padx=18, pady=18)
        self.thumbnail_frame.grid_propagate(False)
        self.thumbnail_label = ctk.CTkLabel(
            self.thumbnail_frame, text="VIDEO\nPREVIEW", text_color="#526683", font=("Segoe UI Semibold", 11)
        )
        self.thumbnail_label.place(relx=0.5, rely=0.5, anchor="center")

        self.media_badge = ctk.CTkLabel(
            self.media_card,
            text="WAITING FOR LINK",
            height=26,
            corner_radius=8,
            fg_color=SURFACE_2,
            text_color=MUTED,
            font=("Segoe UI Semibold", 9),
        )
        self.media_badge.grid(row=0, column=1, sticky="nw", pady=(20, 0), padx=(0, 18))
        self.title_label = ctk.CTkLabel(
            self.media_card,
            text="Analyze media to see its details here",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.title_label.grid(row=1, column=1, sticky="ew", padx=(0, 18), pady=(8, 2))
        self.meta_label = ctk.CTkLabel(
            self.media_card,
            text="Title, creator, platform and duration will appear after analysis.",
            text_color=MUTED,
            font=("Segoe UI", 11),
            anchor="w",
        )
        self.meta_label.grid(row=2, column=1, sticky="new", padx=(0, 18), pady=(2, 18))

    def _build_settings_card(self) -> None:
        card = self._card(self.content)
        card.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        self._section_title(card, "02 / Output", "Choose what you want to save", columnspan=2)

        left = ctk.CTkFrame(card, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(18, 10), pady=(16, 18))
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="DOWNLOAD TYPE", text_color=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=0, column=0, sticky="w"
        )
        self.mode_control = ctk.CTkSegmentedButton(
            left,
            values=["Video", "Audio"],
            variable=self.mode_var,
            command=self._sync_mode,
            height=40,
            corner_radius=11,
            fg_color=SURFACE_2,
            selected_color=PURPLE,
            selected_hover_color=PURPLE_HOVER,
            unselected_color=SURFACE_2,
            unselected_hover_color=SURFACE_3,
            text_color=TEXT,
            font=("Segoe UI Semibold", 11),
        )
        self.mode_control.grid(row=1, column=0, sticky="ew", pady=(7, 15))

        ctk.CTkLabel(left, text="CUSTOM FILE NAME", text_color=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=2, column=0, sticky="w"
        )
        self.name_entry = ctk.CTkEntry(
            left,
            textvariable=self.name_var,
            height=42,
            corner_radius=10,
            fg_color=SURFACE_2,
            border_color="#2A4169",
            border_width=1,
            text_color=TEXT,
            placeholder_text="Video title will appear here",
        )
        self.name_entry.grid(row=3, column=0, sticky="ew", pady=(7, 14))

        ctk.CTkLabel(left, text="SAVE LOCATION", text_color=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=4, column=0, sticky="w"
        )
        folder_row = ctk.CTkFrame(left, fg_color="transparent")
        folder_row.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        folder_row.grid_columnconfigure(0, weight=1)

        self.folder_entry = ctk.CTkEntry(
            folder_row,
            textvariable=self.download_dir_var,
            height=42,
            corner_radius=10,
            fg_color=SURFACE_2,
            border_color="#2A4169",
            border_width=1,
            text_color="#AFC0D9",
            state="readonly",
        )
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.choose_folder_button = ctk.CTkButton(
            folder_row,
            text="Choose folder",
            width=112,
            height=42,
            corner_radius=10,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            border_width=1,
            border_color="#2A4169",
            text_color=TEXT,
            command=self.choose_download_folder,
        )
        self.choose_folder_button.grid(row=0, column=1, padx=(8, 0))
        self.reset_folder_button = ctk.CTkButton(
            folder_row,
            text="Default",
            width=74,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.reset_download_folder,
        )
        self.reset_folder_button.grid(row=0, column=2, padx=(8, 0))

        right = ctk.CTkFrame(card, fg_color=SURFACE_2, corner_radius=14, border_width=1, border_color="#23385A")
        right.grid(row=1, column=1, sticky="nsew", padx=(10, 18), pady=(16, 18))
        right.grid_columnconfigure((0, 1), weight=1)

        self.quality_label = ctk.CTkLabel(
            right, text="VIDEO QUALITY", text_color=MUTED, font=("Segoe UI Semibold", 9)
        )
        self.quality_label.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 0))
        self.video_quality = ctk.CTkComboBox(
            right,
            variable=self.video_quality_var,
            values=["360p", "480p", "720p", "1080p", "Best available"],
            height=39,
            corner_radius=9,
            fg_color=SURFACE_3,
            border_color="#30496F",
            button_color="#263E65",
            button_hover_color=PURPLE,
            dropdown_fg_color=SURFACE_2,
            dropdown_hover_color=SURFACE_3,
            text_color=TEXT,
        )
        self.video_quality.grid(row=1, column=0, sticky="ew", padx=(14, 7), pady=(7, 14))

        self.audio_format_label = ctk.CTkLabel(
            right, text="AUDIO FORMAT", text_color=MUTED, font=("Segoe UI Semibold", 9)
        )
        self.audio_format_label.grid(row=0, column=1, sticky="w", padx=7, pady=(14, 0))
        self.audio_format = ctk.CTkComboBox(
            right,
            variable=self.audio_format_var,
            values=["MP3", "M4A"],
            height=39,
            corner_radius=9,
            fg_color=SURFACE_3,
            border_color="#30496F",
            button_color="#263E65",
            button_hover_color=PURPLE,
            dropdown_fg_color=SURFACE_2,
            dropdown_hover_color=SURFACE_3,
            text_color=TEXT,
        )
        self.audio_format.grid(row=1, column=1, sticky="ew", padx=(7, 14), pady=(7, 14))

        self.audio_quality_label = ctk.CTkLabel(
            right, text="AUDIO BITRATE", text_color=MUTED, font=("Segoe UI Semibold", 9)
        )
        self.audio_quality_label.grid(row=2, column=0, sticky="w", padx=14)
        self.audio_quality = ctk.CTkComboBox(
            right,
            variable=self.audio_quality_var,
            values=["128", "192", "256", "320"],
            height=39,
            corner_radius=9,
            fg_color=SURFACE_3,
            border_color="#30496F",
            button_color="#263E65",
            button_hover_color=PURPLE,
            dropdown_fg_color=SURFACE_2,
            dropdown_hover_color=SURFACE_3,
            text_color=TEXT,
        )
        self.audio_quality.grid(row=3, column=0, sticky="ew", padx=(14, 7), pady=(7, 14))
        self.output_hint = ctk.CTkLabel(
            right,
            text="MP4 video\nFFmpeg merge when needed",
            text_color="#6F83A2",
            font=("Segoe UI", 9),
            justify="left",
        )
        self.output_hint.grid(row=2, column=1, rowspan=2, sticky="sw", padx=(7, 14), pady=(0, 15))

    def _build_download_card(self) -> None:
        card = self._card(self.content)
        card.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        header.grid_columnconfigure(1, weight=1)
        self.status_chip = ctk.CTkLabel(
            header,
            text="READY",
            height=28,
            corner_radius=9,
            fg_color="#0D2A2A",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 9),
        )
        self.status_chip.grid(row=0, column=0, sticky="w")
        self.status_label = ctk.CTkLabel(
            header,
            text="Ready to download",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="e",
        )
        self.status_label.grid(row=0, column=1, sticky="e")

        primary_actions = ctk.CTkFrame(card, fg_color="transparent")
        primary_actions.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))
        primary_actions.grid_columnconfigure(0, weight=1)

        self.download_button = ctk.CTkButton(
            primary_actions,
            text="Download video",
            height=54,
            corner_radius=13,
            fg_color=CYAN,
            hover_color=CYAN_HOVER,
            text_color="#031018",
            font=("Segoe UI Semibold", 13),
            command=self.download,
        )
        self.download_button.grid(row=0, column=0, sticky="ew", padx=(0, 14))

        self.open_editor_after_check = ctk.CTkCheckBox(
            primary_actions,
            text="Open in editor after download",
            variable=self.open_editor_after_var,
            width=210,
            checkbox_width=22,
            checkbox_height=22,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            border_color=BORDER,
            text_color=TEXT,
            font=("Segoe UI", 11),
        )
        self.open_editor_after_check.grid(row=0, column=1, sticky="e")

        self.progress = ctk.CTkProgressBar(card, height=10, corner_radius=6, fg_color=SURFACE_3, progress_color=PURPLE)
        self.progress.grid(row=2, column=0, sticky="ew", padx=18)
        self.progress.set(0)

        progress_row = ctk.CTkFrame(card, fg_color="transparent")
        progress_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 16))
        progress_row.grid_columnconfigure(0, weight=1)
        self.progress_label = ctk.CTkLabel(
            progress_row, text="0%", text_color=TEXT, font=("Segoe UI Semibold", 10)
        )
        self.progress_label.grid(row=0, column=0, sticky="w")
        self.speed_label = ctk.CTkLabel(progress_row, text="", text_color=MUTED, font=("Segoe UI", 10))
        self.speed_label.grid(row=0, column=1, sticky="e")

        self.cancel_job_button = ctk.CTkButton(
            progress_row,
            text="Cancel task",
            width=92,
            height=30,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.cancel_current_job,
            state="disabled",
        )
        self.cancel_job_button.grid(row=0, column=2, sticky="e", padx=(12, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))
        for index in range(3):
            actions.grid_columnconfigure(index, weight=1)

        self.open_file_button = ctk.CTkButton(
            actions,
            text="Open last file",
            height=40,
            corner_radius=10,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self.open_last_file,
            state="disabled",
        )
        self.open_file_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="Open downloads",
            height=40,
            corner_radius=10,
            fg_color=SURFACE_2,
            hover_color=SURFACE_3,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self.open_download_folder,
        ).grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(
            actions,
            text="Clear workspace",
            height=40,
            corner_radius=10,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.clear_form,
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _build_update_card(self) -> None:
        self.update_card = self._card(self.content)
        card = self.update_card
        card.grid(row=5, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        self._section_title(card, "03 / Updates", "Update status")

        self.update_toggle_button = ctk.CTkButton(
            card,
            text="Show details",
            width=108,
            height=32,
            corner_radius=9,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.toggle_update_panel,
        )
        self.update_toggle_button.grid(row=0, column=0, sticky="e", padx=18, pady=(4, 0))

        self.update_body = ctk.CTkFrame(card, fg_color="transparent")
        body = self.update_body
        body.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 18))
        body.grid_columnconfigure(0, weight=1)

        version_row = ctk.CTkFrame(body, fg_color="transparent")
        version_row.grid(row=0, column=0, sticky="ew")
        version_row.grid_columnconfigure(0, weight=1)
        self.update_status_label = ctk.CTkLabel(
            version_row,
            text=f"Current version: v{APP_VERSION}",
            text_color=TEXT,
            font=("Segoe UI Semibold", 11),
            anchor="w",
        )
        self.update_status_label.grid(row=0, column=0, sticky="w")
        self.update_check_button = ctk.CTkButton(
            version_row,
            text="Check for updates",
            width=132,
            height=36,
            corner_radius=9,
            fg_color=SURFACE_3,
            hover_color="#1B3153",
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=lambda: self.check_for_updates(manual=True),
        )
        self.update_check_button.grid(row=0, column=1, sticky="e")

        self.update_detail_label = ctk.CTkLabel(
            body,
            text="Automatic update checks are enabled." if self.auto_check_updates_var.get() else "Automatic update checks are disabled.",
            text_color=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
            wraplength=940,
        )
        self.update_detail_label.grid(row=1, column=0, sticky="ew", pady=(7, 10))

        self.update_notes_label = ctk.CTkLabel(
            body,
            text="",
            text_color="#AFC0D9",
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
            wraplength=940,
        )
        self.update_notes_label.grid(row=2, column=0, sticky="ew")

        self.update_progress = ctk.CTkProgressBar(
            body,
            height=8,
            corner_radius=6,
            fg_color=SURFACE_3,
            progress_color=CYAN,
        )
        self.update_progress.grid(row=3, column=0, sticky="ew", pady=(10, 6))
        self.update_progress.set(0)

        preferences_hint = ctk.CTkFrame(body, fg_color="transparent")
        preferences_hint.grid(row=4, column=0, sticky="ew", pady=(7, 0))
        preferences_hint.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            preferences_hint,
            text="Automatic update preferences are managed in Settings.",
            text_color=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            preferences_hint,
            text="Open Settings",
            width=100,
            height=30,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self.open_settings,
        ).grid(row=0, column=1, sticky="e")

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        buttons.grid_columnconfigure(0, weight=1)
        self.update_action_button = ctk.CTkButton(
            buttons,
            text="Update Now",
            width=150,
            height=40,
            corner_radius=10,
            fg_color=PURPLE,
            hover_color=PURPLE_HOVER,
            text_color="#FFFFFF",
            font=("Segoe UI Semibold", 10),
            command=self.download_or_install_update,
            state="disabled",
        )
        self.update_action_button.grid(row=0, column=0, sticky="w")
        self.update_later_button = ctk.CTkButton(
            buttons,
            text="Later",
            width=80,
            height=40,
            corner_radius=10,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.snooze_update,
            state="disabled",
        )
        self.update_later_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.update_cancel_button = ctk.CTkButton(
            buttons,
            text="Cancel download",
            width=112,
            height=40,
            corner_radius=10,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.cancel_update_download,
            state="disabled",
        )
        self.update_cancel_button.grid(row=0, column=2, sticky="w", padx=(8, 0))

        self.release_button = ctk.CTkButton(
            buttons,
            text="Open latest release",
            width=105,
            height=40,
            corner_radius=10,
            fg_color="transparent",
            hover_color=SURFACE_2,
            border_width=1,
            border_color=BORDER,
            text_color=MUTED,
            command=self.open_release_page,
            state="normal",
        )
        self.release_button.grid(row=0, column=3, sticky="w", padx=(8, 0))

        self.update_body.grid_remove()

    def toggle_update_panel(self) -> None:
        self.update_expanded = not self.update_expanded
        if self.update_expanded:
            self.update_body.grid()
            self.update_toggle_button.configure(text="Hide details")
        else:
            self.update_body.grid_remove()
            self.update_toggle_button.configure(text="Show details")

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self.content, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", pady=(2, 12))
        footer.grid_columnconfigure(0, weight=1)
        self.save_location_label = ctk.CTkLabel(
            footer,
            text=f"SAVE LOCATION  •  {self.download_dir}",
            text_color="#637696",
            font=("Segoe UI", 9),
        )
        self.save_location_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer,
            text="Diagnostics",
            width=108,
            height=26,
            fg_color="transparent",
            hover_color=SURFACE_2,
            text_color=MUTED,
            font=("Segoe UI", 9),
            command=self.open_diagnostics,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ctk.CTkLabel(
            footer,
            text="Use only for content you own or have permission to download.",
            text_color="#637696",
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkButton(
            footer,
            text="Developed by Md Omar Faruk  •  GitHub ↗",
            width=220,
            height=26,
            fg_color="transparent",
            hover_color=SURFACE_2,
            text_color=CYAN,
            font=("Segoe UI", 9),
            command=lambda: webbrowser.open("https://github.com/umar-vai"),
        ).grid(row=1, column=1, sticky="e", pady=(5, 0))

    def _sync_mode(self, value: str | None = None) -> None:
        mode = value or self.mode_var.get()
        is_video = mode == "Video"
        self.mode_var.set(mode)
        self.video_quality.configure(state="normal" if is_video else "disabled")
        self.audio_format.configure(state="disabled" if is_video else "normal")
        self.audio_quality.configure(state="disabled" if is_video else "normal")
        self.quality_label.configure(text_color=TEXT if is_video else "#50617C")
        self.audio_format_label.configure(text_color="#50617C" if is_video else TEXT)
        self.audio_quality_label.configure(text_color="#50617C" if is_video else TEXT)
        self.download_button.configure(text="Download video" if is_video else "Download audio")
        self.output_hint.configure(
            text="MP4 video\nFFmpeg merge when needed" if is_video else "Audio-only export\nChoose format + bitrate"
        )

    def paste_from_clipboard(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if text:
            self.url_var.set(text)
            self.url_entry.focus_set()
            self._on_url_changed()

    def _cancel_auto_analyze(self) -> None:
        if self.url_auto_after_id is None:
            return
        try:
            self.after_cancel(self.url_auto_after_id)
        except Exception:
            pass
        self.url_auto_after_id = None

    def _on_url_changed(self, _event: Any = None) -> None:
        if self.is_busy:
            return
        self._cancel_auto_analyze()
        url = self.url_var.get().strip()

        if url != self.last_analyzed_url:
            # Once the user edits an analyzed URL, invalidate that snapshot.
            # This also lets returning to the same URL trigger a fresh analysis.
            if self.last_analyzed_url:
                self.last_analyzed_url = ""
            self.current_info = None
            self.name_var.set("")
            self.title_label.configure(text="Ready to analyze this link" if url else "Analyze media to see its details here")
            self.meta_label.configure(
                text="Media details will load automatically."
                if url
                else "Title, creator, platform and duration will appear after analysis."
            )
            self.media_badge.configure(
                text="LINK READY" if detect_platform(url) else "WAITING FOR LINK",
                fg_color=SURFACE_2,
                text_color=CYAN if detect_platform(url) else MUTED,
            )
            self._apply_thumbnail(None)

        if (
            bool(self.settings.get("auto_analyze_links", True))
            and detect_platform(url)
            and url != self.last_analyzed_url
        ):
            self.url_auto_after_id = self.after(650, self._auto_analyze_now)

    def _auto_analyze_now(self) -> None:
        self.url_auto_after_id = None
        url = self.url_var.get().strip()
        if self.is_busy or not detect_platform(url) or url == self.last_analyzed_url:
            return
        self.analyze_media()

    def _begin_job(self, state: TaskState) -> tuple[int, threading.Event]:
        self._job_counter += 1
        job_id = self._job_counter
        cancel_event = threading.Event()
        self.active_job_id = job_id
        self.active_job_cancel = cancel_event
        self.task_state = state
        LOGGER.info("Job %s started state=%s", job_id, state.value)
        return job_id, cancel_event

    def _is_current_job(self, job_id: int) -> bool:
        return self.active_job_id == job_id

    def _put_job_event(self, kind: str, job_id: int, data: Any = None) -> None:
        self.events.put((kind, {"job_id": job_id, "data": data}))

    def _finish_job(self, job_id: int) -> None:
        if self.active_job_id != job_id:
            return
        LOGGER.info("Job %s finished state=%s", job_id, self.task_state.value)
        self.active_job_id = None
        self.active_job_cancel = None
        self._set_busy(False)

    def cancel_current_job(self) -> None:
        event = self.active_job_cancel
        if not self.is_busy or event is None:
            return
        event.set()
        self.cancel_job_button.configure(state="disabled", text="Cancelling…")
        self._set_status("Cancelling current task…", "working")
        LOGGER.info("Cancellation requested for job %s", self.active_job_id)

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        self.read_button.configure(state=state)
        self.paste_button.configure(state=state)
        self.url_entry.configure(state=state)
        self.mode_control.configure(state=state)
        self.name_entry.configure(state=state)
        self.download_button.configure(state=state)
        self.open_editor_after_check.configure(state=state)
        self.edit_local_button.configure(state=state)
        self.choose_folder_button.configure(state=state)
        self.reset_folder_button.configure(state=state)
        if busy:
            self.video_quality.configure(state="disabled")
            self.audio_format.configure(state="disabled")
            self.audio_quality.configure(state="disabled")
        else:
            self._sync_mode(self.mode_var.get())
        if hasattr(self, "cancel_job_button"):
            self.cancel_job_button.configure(
                state="normal" if busy else "disabled",
                text="Cancel task",
            )

    def _set_status(self, text: str, kind: str = "ready") -> None:
        palette = {
            "ready": ("#0D2A2A", SUCCESS, "READY"),
            "working": ("#162344", CYAN, "WORKING"),
            "success": ("#0E3025", SUCCESS, "COMPLETE"),
            "cancelled": ("#2D2514", WARNING, "CANCELLED"),
            "error": ("#351722", DANGER, "FAILED"),
        }
        bg, fg, chip = palette.get(kind, palette["ready"])
        self.status_chip.configure(text=chip, fg_color=bg, text_color=fg)
        self.status_label.configure(text=text)

    def analyze_media(self) -> None:
        if self.is_busy:
            return
        self._cancel_auto_analyze()
        url = self.url_var.get().strip()
        platform = detect_platform(url)
        if not platform:
            messagebox.showerror(APP_NAME, "Please paste a valid YouTube, Facebook or Instagram URL.")
            return

        job_id, cancel_event = self._begin_job(TaskState.ANALYZING)
        self._set_busy(True)
        self.current_info = None
        self.title_label.configure(text=f"Analyzing {platform_name(platform)} media…")
        self.meta_label.configure(text="Trying compatible connection paths…")
        self._apply_thumbnail(None)
        self._set_status(f"Reading {platform_name(platform)} media information…", "working")
        self.media_badge.configure(
            text=f"{platform_name(platform).upper()} • ANALYZING",
            fg_color="#162344",
            text_color=CYAN,
        )
        threading.Thread(
            target=self._analyze_media_worker,
            args=(job_id, cancel_event, url),
            daemon=True,
        ).start()

    def _analyze_media_worker(self, job_id: int, cancel_event: threading.Event, url: str) -> None:
        try:
            attempts = extraction_attempts(url)
            info: dict[str, Any] = {}
            last_error: Exception | None = None
            for index, (attempt_url, network_options) in enumerate(attempts, start=1):
                if cancel_event.is_set():
                    self._put_job_event("cancelled", job_id, "Analysis cancelled.")
                    return
                if len(attempts) > 1:
                    self._put_job_event(
                        "status",
                        job_id,
                        f"Facebook connection attempt {index}/{len(attempts)}…",
                    )
                try:
                    with yt_dlp.YoutubeDL(
                        {
                            "quiet": True,
                            "no_warnings": True,
                            "skip_download": True,
                            "noplaylist": True,
                            "cachedir": False,
                            "socket_timeout": 20,
                            "retries": 1,
                            "fragment_retries": 1,
                            **network_options,
                        }
                    ) as ydl:
                        info = ydl.extract_info(attempt_url, download=False) or {}
                    if info:
                        break
                except Exception as exc:
                    last_error = exc

            if cancel_event.is_set():
                self._put_job_event("cancelled", job_id, "Analysis cancelled.")
                return
            if not info:
                raise last_error or RuntimeError("No compatible connection path succeeded.")

            thumb_bytes = None
            thumbnail_url = str(info.get("thumbnail") or "")
            if thumbnail_url and not cancel_event.is_set():
                try:
                    request = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        thumb_bytes = response.read(2_500_000)
                except Exception:
                    thumb_bytes = None

            if cancel_event.is_set():
                self._put_job_event("cancelled", job_id, "Analysis cancelled.")
                return

            self._put_job_event(
                "info",
                job_id,
                {
                    "title": str(info.get("title") or info.get("description") or "Media"),
                    "channel": str(info.get("channel") or info.get("uploader") or info.get("uploader_id") or "Creator"),
                    "duration": format_duration(info.get("duration")),
                    "views": info.get("view_count"),
                    "platform": detect_platform(url) or str(info.get("extractor_key") or "media").lower(),
                    "info": info,
                    "thumbnail": thumb_bytes,
                },
            )
        except Exception as exc:
            LOGGER.exception("Analysis job %s failed", job_id)
            self._put_job_event(
                "error",
                job_id,
                "Could not read this media link after trying the available connection paths. "
                "Public links work best; private or login-required content is not supported.\n\n"
                + str(exc),
            )
        finally:
            self._put_job_event("analysis_finished", job_id)

    def download(self) -> None:
        self._start_download(edit_after_download=bool(self.open_editor_after_var.get()))

    def download_for_editing(self) -> None:
        self._start_download(edit_after_download=True)

    def _start_download(self, edit_after_download: bool) -> None:
        if self.is_busy:
            return
        url = self.url_var.get().strip()
        platform = detect_platform(url)
        if not platform:
            messagebox.showerror(APP_NAME, "Please paste a valid YouTube, Facebook or Instagram URL.")
            return

        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.exception("Could not create download directory")
            messagebox.showerror(
                APP_NAME,
                f"Could not use the selected download folder.\n\n{exc}",
            )
            return

        name = safe_filename(self.name_var.get(), "media_download")
        info = self.current_info if isinstance(self.current_info, dict) else {}
        try:
            history_duration = max(0, int(float(info.get("duration") or 0)))
        except (TypeError, ValueError):
            history_duration = 0
        mode = self.mode_var.get()
        audio_format = self.audio_format_var.get()
        audio_quality = self.audio_quality_var.get()
        video_quality = self.video_quality_var.get()
        request_settings = {
            "mode": mode,
            "video_quality": video_quality,
            "audio_format": audio_format.lower(),
            "audio_quality": audio_quality,
            "edit_after_download": bool(edit_after_download),
            "history_title": str(info.get("title") or self.name_var.get() or name),
            "history_creator": str(info.get("channel") or info.get("uploader") or info.get("creator") or ""),
            "history_platform": platform,
            "history_duration": history_duration,
            "history_quality": (
                str(video_quality)
                if mode == "Video"
                else f"{audio_format.upper()} {audio_quality} kbps"
            ),
            "source_url": url,
        }
        job_id, cancel_event = self._begin_job(TaskState.DOWNLOADING)
        self.edit_after_download = bool(edit_after_download)
        self.progress.set(0)
        self.progress_label.configure(text="0%")
        self.speed_label.configure(text="Preparing editor…" if edit_after_download else "Starting…")
        self._set_status(
            f"Downloading from {platform_name(platform)} for editing…" if edit_after_download
            else f"Connecting to {platform_name(platform)}…",
            "working",
        )
        self._set_busy(True)
        threading.Thread(
            target=self._download_worker,
            args=(job_id, cancel_event, url, name, self.download_dir, request_settings),
            daemon=True,
        ).start()

    def _download_worker(
        self,
        job_id: int,
        cancel_event: threading.Event,
        url: str,
        name: str,
        download_dir: Path,
        request_settings: dict[str, Any],
    ) -> None:
        started_at = time.time()

        def hook(data: dict[str, Any]) -> None:
            if cancel_event.is_set():
                raise RuntimeError("Download cancelled by user.")
            status = data.get("status")
            if status == "downloading":
                downloaded = int(data.get("downloaded_bytes") or 0)
                total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                percent = (downloaded / total * 100.0) if total else 0.0
                speed = human_bytes(data.get("speed"))
                eta = data.get("eta")
                extras = []
                if speed:
                    extras.append(f"{speed}/s")
                if eta is not None:
                    try:
                        extras.append(f"ETA {int(eta)}s")
                    except Exception:
                        pass
                self._put_job_event(
                    "progress",
                    job_id,
                    {"percent": percent, "detail": "  •  ".join(extras)},
                )
            elif status == "finished":
                self._put_job_event("status", job_id, "Download finished. Finalizing file…")

        opts: dict[str, Any] = {
            "outtmpl": str(download_dir / f"{name}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "cachedir": False,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 4,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [hook],
            "overwrites": True,
            "ffmpeg_location": get_ffmpeg_exe(),
        }

        if request_settings["mode"] == "Audio":
            opts.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": request_settings["audio_format"],
                            "preferredquality": request_settings["audio_quality"],
                        }
                    ],
                }
            )
        else:
            opts["format"] = video_format_selector(url, str(request_settings["video_quality"]))
            opts["merge_output_format"] = "mp4"

        try:
            attempts = extraction_attempts(url)
            last_error: Exception | None = None
            downloaded = False
            for index, (attempt_url, network_options) in enumerate(attempts, start=1):
                if cancel_event.is_set():
                    self._put_job_event("cancelled", job_id, "Download cancelled.")
                    return
                if len(attempts) > 1:
                    self._put_job_event(
                        "status",
                        job_id,
                        f"Facebook download connection {index}/{len(attempts)}…",
                    )
                attempt_opts = {**opts, **network_options}
                try:
                    with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                        ydl.extract_info(attempt_url, download=True)
                    downloaded = True
                    break
                except Exception as exc:
                    last_error = exc
                    for partial in download_dir.glob(f"{name}.*"):
                        if partial.suffix.lower() in {".part", ".ytdl", ".temp", ".tmp"}:
                            try:
                                partial.unlink()
                            except OSError:
                                pass
                    if cancel_event.is_set():
                        self._put_job_event("cancelled", job_id, "Download cancelled.")
                        return

            if not downloaded:
                raise last_error or RuntimeError("No compatible connection path succeeded.")

            candidates = []
            for path in download_dir.glob(f"{name}.*"):
                if not path.is_file() or path.suffix.lower() in {".part", ".ytdl", ".temp", ".tmp"}:
                    continue
                try:
                    if path.stat().st_mtime >= started_at - 2.0:
                        candidates.append(path)
                except OSError:
                    continue

            if not candidates:
                raise RuntimeError("Download finished, but the final file could not be located.")

            final_path = max(candidates, key=lambda path: path.stat().st_mtime)
            self._put_job_event(
                "done",
                job_id,
                {
                    "path": str(final_path),
                    "title": request_settings.get("history_title") or name,
                    "source_url": request_settings.get("source_url") or url,
                    "platform": request_settings.get("history_platform") or "",
                    "creator": request_settings.get("history_creator") or "",
                    "mode": request_settings.get("mode") or "Video",
                    "quality": request_settings.get("history_quality") or "",
                    "duration_seconds": request_settings.get("history_duration") or 0,
                },
            )
        except Exception as exc:
            if cancel_event.is_set():
                self._put_job_event("cancelled", job_id, "Download cancelled.")
            else:
                LOGGER.exception("Download job %s failed", job_id)
                self._put_job_event("error", job_id, str(exc))
        finally:
            self._put_job_event("download_finished", job_id)

    def _apply_thumbnail(self, raw: bytes | None) -> None:
        if not raw:
            self.thumbnail_image = None
            self.thumbnail_label.configure(image=None, text="VIDEO\nPREVIEW")
            return
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            target = (214, 122)
            image.thumbnail(target, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", target, (16, 28, 49))
            canvas.paste(image, ((target[0] - image.width) // 2, (target[1] - image.height) // 2))
            self.thumbnail_image = ctk.CTkImage(
                light_image=canvas,
                dark_image=canvas,
                size=target,
            )
            self.thumbnail_label.configure(image=self.thumbnail_image, text="")
        except Exception:
            self.thumbnail_image = None
            self.thumbnail_label.configure(image=None, text="VIDEO\nPREVIEW")

    def _drain_events(self) -> None:
        job_kinds = {
            "info",
            "progress",
            "status",
            "done",
            "error",
            "cancelled",
            "analysis_finished",
            "download_finished",
        }
        try:
            while True:
                kind, payload = self.events.get_nowait()
                data = payload

                if kind in job_kinds:
                    if not isinstance(payload, dict):
                        LOGGER.warning("Ignoring malformed job event kind=%s payload=%r", kind, payload)
                        continue
                    job_id = int(payload.get("job_id") or -1)
                    if not self._is_current_job(job_id):
                        LOGGER.info(
                            "Ignoring stale job event kind=%s job=%s active=%s",
                            kind,
                            job_id,
                            self.active_job_id,
                        )
                        continue
                    data = payload.get("data")

                if kind == "info":
                    media = dict(data or {})
                    self.current_info = media.get("info")
                    self.last_analyzed_url = self.url_var.get().strip()
                    self.name_var.set(safe_filename(str(media.get("title") or "Media")))
                    self.title_label.configure(text=str(media.get("title") or "Media"))
                    platform_label = platform_name(str(media.get("platform") or ""))
                    self.meta_label.configure(
                        text=f"{media.get('channel') or 'Creator'}  •  {platform_label}  •  {media.get('duration') or '--:--'}"
                    )
                    self.media_badge.configure(
                        text=f"{platform_label.upper()} • READY",
                        fg_color="#0E3025",
                        text_color=SUCCESS,
                    )
                    self._apply_thumbnail(media.get("thumbnail"))
                    self.task_state = TaskState.READY
                    self._set_status(f"{platform_label} media information loaded", "ready")
                    self._finish_job(job_id)

                elif kind == "progress":
                    detail = dict(data or {})
                    percent = max(0.0, min(100.0, float(detail.get("percent") or 0)))
                    self.progress.set(percent / 100.0)
                    self.progress_label.configure(text=f"{percent:.0f}%")
                    self.speed_label.configure(text=str(detail.get("detail") or "Downloading…"))
                    self._set_status(f"Downloading… {percent:.1f}%", "working")

                elif kind == "status":
                    self._set_status(str(data or ""), "working")

                elif kind == "done":
                    detail = dict(data) if isinstance(data, dict) else {"path": str(data)}
                    completed_path = Path(str(detail.get("path") or ""))
                    self.last_file = completed_path
                    self.progress.set(1)
                    self.progress_label.configure(text="100%")
                    self.speed_label.configure(text=completed_path.name)
                    self.task_state = TaskState.DOWNLOADED
                    self._set_status("Download completed successfully", "success")

                    try:
                        entry = make_history_entry(
                            completed_path,
                            title=str(detail.get("title") or completed_path.stem),
                            source_url=str(detail.get("source_url") or ""),
                            platform=str(detail.get("platform") or ""),
                            creator=str(detail.get("creator") or ""),
                            mode=str(detail.get("mode") or "Video"),
                            quality=str(detail.get("quality") or ""),
                            duration_seconds=int(detail.get("duration_seconds") or 0),
                        )
                        self.history_store.add(entry)
                        LOGGER.info("Download history recorded file=%s", completed_path)
                    except Exception:
                        LOGGER.exception("Could not save download history file=%s", completed_path)

                    self._sync_recent_file()
                    if self.history_window is not None:
                        try:
                            if self.history_window.winfo_exists():
                                self.history_window.refresh()
                        except Exception:
                            self.history_window = None

                    should_edit = self.edit_after_download
                    self.edit_after_download = False
                    self._finish_job(job_id)
                    if should_edit:
                        self.after(200, lambda path=completed_path: self.open_editor(path))

                elif kind == "error":
                    self.task_state = TaskState.ERROR
                    self._set_status("Task failed", "error")
                    self.speed_label.configure(text="")
                    self.edit_after_download = False
                    LOGGER.error("Job %s failed: %s", job_id, data)
                    self._finish_job(job_id)
                    messagebox.showerror(APP_NAME, str(data))

                elif kind == "cancelled":
                    self.task_state = TaskState.CANCELLED
                    self.edit_after_download = False
                    self.speed_label.configure(text="Cancelled")
                    self._set_status(str(data or "Task cancelled"), "cancelled")
                    self._finish_job(job_id)

                elif kind in {"analysis_finished", "download_finished"}:
                    # Safety net only. Success/error/cancel normally finish the
                    # job first; stale final events are ignored by job_id.
                    self._finish_job(job_id)

                elif kind == "update_available":
                    self._handle_update_available(payload)
                elif kind == "update_current":
                    self._handle_update_current(payload)
                elif kind == "update_error":
                    self._handle_update_error(payload)
                elif kind == "update_progress":
                    self._handle_update_progress(payload)
                elif kind == "update_ready":
                    self._handle_update_ready(payload)
                elif kind == "update_cancelled":
                    self._handle_update_cancelled()

        except queue.Empty:
            pass
        except Exception:
            LOGGER.exception("UI event pump error")
        finally:
            self.after(120, self._drain_events)

    def choose_download_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose download folder",
            initialdir=str(self.download_dir if self.download_dir.exists() else self.download_dir.parent),
            mustexist=True,
        )
        if selected:
            self.download_dir = Path(selected)
            self.download_dir_var.set(str(self.download_dir))
            self.settings["download_dir"] = str(self.download_dir)
            save_settings(self.settings)
            self.save_location_label.configure(text=f"SAVE LOCATION  •  {self.download_dir}")

    def reset_download_folder(self) -> None:
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        self.download_dir_var.set(str(self.download_dir))
        self.settings["download_dir"] = str(self.download_dir)
        save_settings(self.settings)
        self.save_location_label.configure(text=f"SAVE LOCATION  •  {self.download_dir}")

    def open_download_folder(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(self.download_dir))
        else:
            subprocess.Popen(["xdg-open", str(self.download_dir)])

    def open_editor(self, source_path: Path | None = None) -> None:
        if self.is_busy:
            return

        existing = self.editor_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    messagebox.showinfo(
                        APP_NAME,
                        "An editor window is already open. Close it before opening another file.",
                        parent=self,
                    )
                    return
            except Exception:
                self.editor_window = None

        path = Path(source_path) if source_path else None
        if path is None:
            selected = filedialog.askopenfilename(
                title="Choose video or audio to edit",
                initialdir=str(self.download_dir if self.download_dir.exists() else Path.home()),
                filetypes=[
                    ("Media files", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.mp3 *.m4a *.wav *.aac *.flac *.ogg *.opus"),
                    ("Video files", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v"),
                    ("Audio files", "*.mp3 *.m4a *.wav *.aac *.flac *.ogg *.opus"),
                    ("All files", "*.*"),
                ],
            )
            if not selected:
                return
            path = Path(selected)
        if not path.exists():
            messagebox.showerror(APP_NAME, "The selected media file could not be found.")
            return
        try:
            self.editor_window = MediaEditorWindow(self, path, output_dir=self.download_dir)
            self.editor_window.bind(
                "<Destroy>",
                lambda event: self._editor_destroyed(event),
                add="+",
            )
            self.editor_window.focus()
            LOGGER.info("Editor opened source=%s", path)
        except Exception as exc:
            LOGGER.exception("Could not open Media Editor source=%s", path)
            self.editor_window = None
            messagebox.showerror(APP_NAME, f"Could not open Media Editor.\n\n{exc}")

    def _editor_destroyed(self, event: Any) -> None:
        window = self.editor_window
        if window is not None and event.widget is window:
            self.editor_window = None
            LOGGER.info("Editor window closed")

    def open_last_file(self) -> None:
        if not self.last_file or not self.last_file.exists():
            self._sync_recent_file()
        if self.last_file and self.last_file.exists():
            if os.name == "nt":
                os.startfile(str(self.last_file))
            else:
                subprocess.Popen(["xdg-open", str(self.last_file)])
        else:
            self.open_history()

    def clear_form(self) -> None:
        self._cancel_auto_analyze()
        self.last_analyzed_url = ""
        self.open_editor_after_var.set(bool(self.settings.get("open_editor_after_download", False)))
        self.task_state = TaskState.IDLE
        self.active_job_id = None
        self.active_job_cancel = None
        self.url_var.set("")
        self.name_var.set("")
        self.current_info = None
        self.progress.set(0)
        self.progress_label.configure(text="0%")
        self.speed_label.configure(text="")
        self.title_label.configure(text="Analyze media to see its details here")
        self.meta_label.configure(text="Title, creator, platform and duration will appear after analysis.")
        self.media_badge.configure(text="WAITING FOR LINK", fg_color=SURFACE_2, text_color=MUTED)
        self._apply_thumbnail(None)
        self._set_status("Ready to download", "ready")
        self._set_busy(False)

    def _save_update_preferences(self) -> None:
        self.settings["auto_check_updates"] = bool(self.auto_check_updates_var.get())
        self.settings["auto_download_updates"] = bool(self.auto_download_updates_var.get())
        save_settings(self.settings)
        if not self.latest_release:
            text = (
                "Automatic update checks are enabled."
                if self.auto_check_updates_var.get()
                else "Automatic update checks are disabled."
            )
            self.update_detail_label.configure(text=text)

    def check_for_updates(self, manual: bool = True) -> None:
        if self.update_checking:
            return
        self.update_checking = True
        self.update_check_button.configure(text="Checking…", state="disabled")
        self.update_detail_label.configure(text="Checking GitHub Releases for a newer version…")
        append_update_log(f"Checking for updates. Current version={APP_VERSION}")
        threading.Thread(target=self._check_for_updates_worker, args=(manual,), daemon=True).start()

    def _check_for_updates_worker(self, manual: bool) -> None:
        try:
            release = fetch_latest_release()
            if is_newer_version(release.version, APP_VERSION):
                self.events.put(("update_available", {"release": release, "manual": manual}))
            else:
                self.events.put(("update_current", {"release": release, "manual": manual}))
        except Exception as exc:
            self.events.put(("update_error", {"error": str(exc), "manual": manual}))

    def _is_update_snoozed(self, version: str) -> bool:
        snooze_version = str(self.settings.get("snooze_version") or "")
        try:
            snooze_until = float(self.settings.get("snooze_until") or 0)
        except (TypeError, ValueError):
            snooze_until = 0
        return snooze_version == version and time.time() < snooze_until

    def _handle_update_available(self, payload: dict[str, Any]) -> None:
        self.update_checking = False
        self.update_check_button.configure(text="Check for updates", state="normal")
        release = payload["release"]
        manual = bool(payload.get("manual"))
        self.latest_release = release
        self.update_status_label.configure(text=f"Update available: v{release.version}  •  Current: v{APP_VERSION}")
        notes = release.notes.strip()
        if len(notes) > 650:
            notes = notes[:647].rstrip() + "…"
        self.update_notes_label.configure(text=notes or "A new Team Fahad Downloader update is ready.")
        self.update_detail_label.configure(text="Verified update metadata found on GitHub Releases.")
        self.update_action_button.configure(text="Update Now", state="normal")
        self.update_later_button.configure(state="normal")
        self.release_button.configure(
            text="Open latest release",
            command=self.open_release_page,
            state="normal",
        )
        append_update_log(f"Update available: {release.version}")

        if manual or not self._is_update_snoozed(release.version):
            self.top_update_button.configure(text=f"UPDATE v{release.version}")
            self.top_update_button.grid()

        if self.auto_download_updates_var.get() and not self.downloaded_update:
            self._start_update_download(manual=False)

    def _handle_update_current(self, payload: dict[str, Any]) -> None:
        self.update_checking = False
        self.update_check_button.configure(text="Check for updates", state="normal")
        self.update_status_label.configure(text=f"Current version: v{APP_VERSION}")
        self.update_detail_label.configure(text="You’re up to date.")
        self.update_notes_label.configure(text="")
        self.update_progress.set(0)
        self.top_update_button.grid_remove()
        append_update_log("No update available.")
        if payload.get("manual"):
            messagebox.showinfo(APP_NAME, f"You’re up to date.\n\nCurrent version: v{APP_VERSION}")

    def _handle_update_error(self, payload: dict[str, Any]) -> None:
        self.update_checking = False
        self.update_downloading = False
        self.update_check_button.configure(text="Check for updates", state="normal")
        self.update_action_button.configure(state="normal" if self.latest_release else "disabled")
        self.update_cancel_button.configure(state="disabled", text="Cancel download")
        self.update_cancel_event = None

        error = str(payload.get("error") or "Unknown update error").strip()
        display_error = error if len(error) <= 360 else error[:357].rstrip() + "…"
        append_update_log(f"Update error: {error}")

        if self.latest_release:
            self.release_button.configure(
                text="Download in browser",
                command=self.open_update_asset_in_browser,
                state="normal",
            )
            self.update_detail_label.configure(
                text="Update found, but automatic download failed. "
                + display_error
                + "\nUse ‘Download in browser’ to download the verified release manually."
            )
            if payload.get("manual"):
                messagebox.showwarning(
                    APP_NAME,
                    "The update was found, but the automatic download failed.\n\n"
                    + display_error
                    + "\n\nUse ‘Download in browser’ to continue manually.",
                )
        else:
            self.release_button.configure(
                text="Open latest release",
                command=self.open_release_page,
                state="normal",
            )
            self.update_detail_label.configure(
                text="Update check failed. "
                + display_error
                + "\nYou can still open the latest GitHub release manually."
            )
            if payload.get("manual"):
                messagebox.showwarning(
                    APP_NAME,
                    "Update check failed.\n\n"
                    + display_error
                    + "\n\nUse ‘Open latest release’ for a manual update if needed.",
                )

    def _handle_update_progress(self, payload: dict[str, Any]) -> None:
        downloaded = int(payload.get("downloaded") or 0)
        total = int(payload.get("total") or 0)
        if total > 0:
            fraction = max(0.0, min(1.0, downloaded / total))
            percent = fraction * 100
            self.update_progress.set(fraction)
            detail = f"Downloading update… {percent:.0f}%  •  {human_bytes(downloaded)} / {human_bytes(total)}"
        else:
            detail = f"Downloading update… {human_bytes(downloaded)}"
        self.update_detail_label.configure(text=detail)

    def _handle_update_ready(self, payload: dict[str, Any]) -> None:
        self.update_downloading = False
        self.update_cancel_event = None
        self.update_cancel_button.configure(state="disabled")
        self.downloaded_update = Path(str(payload["path"]))
        release = payload["release"]
        self.latest_release = release
        self.update_progress.set(1)
        if is_installed_mode():
            self.update_detail_label.configure(text="Installer update downloaded and verified. Restart to install it.")
        else:
            self.update_detail_label.configure(text="Portable update downloaded and verified. Restart to install it.")
        self.update_action_button.configure(text="Restart & Update", state="normal")
        self.top_update_button.configure(text="RESTART & UPDATE")
        self.top_update_button.grid()
        append_update_log(f"Update {release.version} downloaded and SHA-256 verified.")

    def download_or_install_update(self) -> None:
        if not self.latest_release:
            self.check_for_updates(manual=True)
            return
        if self.downloaded_update and self.downloaded_update.exists():
            self._launch_updater()
            return
        self._start_update_download(manual=True)

    def _start_update_download(self, manual: bool) -> None:
        if self.update_downloading or not self.latest_release:
            return
        self.update_downloading = True
        self.update_cancel_event = threading.Event()
        self.update_action_button.configure(text="Downloading…", state="disabled")
        self.update_cancel_button.configure(state="normal")
        self.update_progress.set(0)
        release = self.latest_release
        cancel_event = self.update_cancel_event
        threading.Thread(
            target=self._download_update_worker,
            args=(release, manual, cancel_event),
            daemon=True,
        ).start()

    def cancel_update_download(self) -> None:
        event = self.update_cancel_event
        if not self.update_downloading or event is None:
            return
        event.set()
        self.update_cancel_button.configure(state="disabled", text="Cancelling…")
        self.update_detail_label.configure(text="Cancelling update download…")
        append_update_log("Update download cancellation requested.")

    def _download_update_worker(
        self,
        release: ReleaseInfo,
        manual: bool,
        cancel_event: threading.Event,
    ) -> None:
        def progress(downloaded: int, total: int) -> None:
            if cancel_event.is_set():
                raise RuntimeError("Update download cancelled by user.")
            self.events.put(("update_progress", {"downloaded": downloaded, "total": total}))

        try:
            version_dir = UPDATE_DIR / release.version
            if is_installed_mode():
                path = download_installer_release(release, version_dir, progress_callback=progress)
            else:
                path = download_release(release, version_dir, progress_callback=progress)
            if cancel_event.is_set():
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
                self.events.put(("update_cancelled", None))
                return
            self.events.put(("update_ready", {"path": str(path), "release": release}))
        except Exception as exc:
            if cancel_event.is_set():
                self.events.put(("update_cancelled", None))
            else:
                self.events.put(("update_error", {"error": str(exc), "manual": manual}))

    def _handle_update_cancelled(self) -> None:
        self.update_downloading = False
        self.update_cancel_event = None
        self.update_progress.set(0)
        self.update_cancel_button.configure(state="disabled", text="Cancel download")
        self.update_action_button.configure(text="Update Now", state="normal")
        self.update_detail_label.configure(text="Update download cancelled. You can resume it later.")
        append_update_log("Update download cancelled.")

    def _launch_updater(self) -> None:
        if not self.latest_release or not self.downloaded_update:
            return
        if not is_frozen_windows_app():
            messagebox.showinfo(
                APP_NAME,
                "The update was downloaded and verified, but automatic replacement only runs in the packaged Windows EXE build.",
            )
            return

        bundled = get_bundled_updater_path()
        if not bundled:
            messagebox.showerror(APP_NAME, "The updater component is missing from this build.")
            append_update_log("Updater component missing from bundled executable.")
            return

        try:
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            updater_copy = UPDATE_DIR / "MediaDownloaderUpdater.exe"
            shutil.copy2(bundled, updater_copy)
            command = [
                str(updater_copy),
                "--target",
                str(Path(sys.executable).resolve()),
                "--source",
                str(self.downloaded_update.resolve()),
                "--pid",
                str(os.getpid()),
                "--version",
                self.latest_release.version,
                "--result-file",
                str(UPDATE_RESULT_FILE.resolve()),
                "--log-file",
                str(UPDATE_LOG_FILE.resolve()),
            ]
            if is_installed_mode():
                command.append("--installer")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(command, close_fds=True, creationflags=creationflags)
            mode = "installer" if is_installed_mode() else "portable"
            append_update_log(f"Updater launched for version {self.latest_release.version} in {mode} mode.")
            self.update_detail_label.configure(text="Restarting to complete the update…")
            self.after(250, self.destroy)
        except Exception as exc:
            append_update_log(f"Could not launch updater: {exc}")
            messagebox.showerror(
                APP_NAME,
                "The updater could not be started. Your current app has not been changed.",
            )

    def snooze_update(self) -> None:
        if not self.latest_release:
            return
        self.settings["snooze_version"] = self.latest_release.version
        self.settings["snooze_until"] = int(time.time() + 24 * 60 * 60)
        save_settings(self.settings)
        self.top_update_button.grid_remove()
        self.update_detail_label.configure(text="Update reminder snoozed for 24 hours.")

    def _sync_recent_file(self) -> None:
        recent = self.history_store.most_recent_existing()
        self.last_file = Path(str(recent["file_path"])) if recent else None
        if hasattr(self, "open_file_button"):
            self.open_file_button.configure(state="normal" if self.last_file else "disabled")

    def open_history(self) -> None:
        existing = self.history_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                self.history_window = None

        try:
            self.history_window = HistoryWindow(
                self,
                self.history_store,
                on_edit=lambda path: self.open_editor(path),
                on_change=self._sync_recent_file,
            )
            self.history_window.bind(
                "<Destroy>",
                lambda event: self._history_destroyed(event),
                add="+",
            )
            self.history_window.focus()
            LOGGER.info("Download history window opened")
        except Exception as exc:
            LOGGER.exception("Could not open download history")
            self.history_window = None
            messagebox.showerror(APP_NAME, f"Could not open Download History.\n\n{exc}")

    def _history_destroyed(self, event: Any) -> None:
        window = self.history_window
        if window is not None and event.widget is window:
            self.history_window = None
            self._sync_recent_file()
            LOGGER.info("Download history window closed")

    def open_settings(self) -> None:
        existing = self.settings_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                self.settings_window = None

        try:
            self.settings_window = SettingsWindow(
                self,
                self.settings,
                on_save=self._apply_settings,
                on_check_updates=lambda: self.check_for_updates(manual=True),
                on_open_diagnostics=self.open_diagnostics,
                on_open_log=self.open_app_log,
                on_open_release=self.open_release_page,
            )
            self.settings_window.bind(
                "<Destroy>",
                lambda event: self._settings_destroyed(event),
                add="+",
            )
            self.settings_window.focus()
            LOGGER.info("Settings window opened")
        except Exception as exc:
            LOGGER.exception("Could not open settings window")
            self.settings_window = None
            messagebox.showerror(APP_NAME, f"Could not open Settings.\n\n{exc}")

    def _settings_destroyed(self, event: Any) -> None:
        window = self.settings_window
        if window is not None and event.widget is window:
            self.settings_window = None
            LOGGER.info("Settings window closed")

    def _apply_settings(self, payload: dict[str, Any]) -> None:
        candidate = dict(self.settings)
        candidate.update(payload)
        normalized = normalize_settings(candidate)

        download_dir = Path(str(normalized["download_dir"])).expanduser()
        if download_dir != self.download_dir:
            try:
                download_dir.mkdir(parents=True, exist_ok=True)
                probe = download_dir / ".media_downloader_write_test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            except Exception as exc:
                raise RuntimeError(f"The selected download folder is not writable: {download_dir}\n\n{exc}") from exc

        normalized["download_dir"] = str(download_dir)
        if not save_settings(normalized):
            raise RuntimeError(f"Could not save settings to {CONFIG_FILE}.")
        self.settings = normalized

        self.download_dir = download_dir
        self.download_dir_var.set(str(download_dir))
        self.mode_var.set(str(normalized["default_mode"]))
        self.video_quality_var.set(str(normalized["video_quality"]))
        self.audio_format_var.set(str(normalized["audio_format"]))
        self.audio_quality_var.set(str(normalized["audio_quality"]))
        self.open_editor_after_var.set(bool(normalized["open_editor_after_download"]))
        self.auto_check_updates_var.set(bool(normalized["auto_check_updates"]))
        self.auto_download_updates_var.set(bool(normalized["auto_download_updates"]))

        self.save_location_label.configure(text=f"SAVE LOCATION  •  {self.download_dir}")
        self._sync_mode(str(normalized["default_mode"]))

        if not self.latest_release:
            self.update_detail_label.configure(
                text=(
                    "Automatic update checks are enabled."
                    if self.auto_check_updates_var.get()
                    else "Automatic update checks are disabled."
                )
            )

        LOGGER.info(
            "Settings applied mode=%s video_quality=%s audio_format=%s download_dir=%s auto_analyze=%s",
            normalized["default_mode"],
            normalized["video_quality"],
            normalized["audio_format"],
            download_dir,
            normalized["auto_analyze_links"],
        )

        if (
            self.latest_release
            and self.auto_download_updates_var.get()
            and not self.downloaded_update
            and not self.update_downloading
        ):
            self.after(150, lambda: self._start_update_download(manual=False))


    def open_diagnostics(self) -> None:
        existing = self.diagnostics_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                self.diagnostics_window = None

        try:
            self.diagnostics_window = DiagnosticsWindow(self)
            self.diagnostics_window.bind(
                "<Destroy>",
                lambda event: self._diagnostics_destroyed(event),
                add="+",
            )
            self.diagnostics_window.focus()
            LOGGER.info("Diagnostics window opened")
        except Exception as exc:
            LOGGER.exception("Could not open diagnostics window")
            self.diagnostics_window = None
            messagebox.showerror(APP_NAME, f"Could not open diagnostics.\n\n{exc}")

    def _diagnostics_destroyed(self, event: Any) -> None:
        window = self.diagnostics_window
        if window is not None and event.widget is window:
            self.diagnostics_window = None
            LOGGER.info("Diagnostics window closed")

    def open_app_log(self) -> None:
        path = log_path()
        try:
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            LOGGER.exception("Could not open diagnostics log")
            messagebox.showerror(APP_NAME, f"Could not open diagnostics log.\n\n{exc}")

    def open_update_asset_in_browser(self) -> None:
        release = self.latest_release
        if release:
            url = release.installer_url if is_installed_mode() and release.installer_url else release.asset_url
            if url:
                append_update_log(
                    f"Opening browser download for version {release.version} ({'installer' if is_installed_mode() else 'portable'} mode)."
                )
                webbrowser.open(url)
                return
        self.open_release_page()

    def open_release_page(self) -> None:
        if self.latest_release and self.latest_release.html_url:
            webbrowser.open(self.latest_release.html_url)
        else:
            webbrowser.open(LATEST_RELEASE_WEB)

    def _show_update_result(self, attempt: int = 0) -> None:
        if not UPDATE_RESULT_FILE.exists():
            if attempt < 14:
                self.after(750, lambda: self._show_update_result(attempt + 1))
            return
        try:
            payload = json.loads(UPDATE_RESULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("Could not read updater result")
            if attempt < 14:
                self.after(750, lambda: self._show_update_result(attempt + 1))
            return
        try:
            UPDATE_RESULT_FILE.unlink(missing_ok=True)
        except Exception:
            LOGGER.exception("Could not remove updater result file")

        status = str(payload.get("status") or "")
        version = str(payload.get("version") or APP_VERSION)
        message = str(payload.get("message") or "")
        LOGGER.info("Updater result status=%s version=%s message=%s", status, version, message)

        if status == "success":
            self.update_status_label.configure(text=f"Updated successfully to v{version}")
            self.update_detail_label.configure(text="The latest update is installed and ready.")
            self.update_progress.set(1)
            self.top_update_button.grid_remove()
            messagebox.showinfo(APP_NAME, f"Updated successfully to v{version}.")
        elif status == "failed":
            self.update_detail_label.configure(
                text="The update could not be installed. The previous version was restored."
            )
            messagebox.showwarning(
                APP_NAME,
                "The update could not be installed. Your previous working version was restored."
                + (f"\n\n{message}" if message else ""),
            )


if __name__ == "__main__":
    DownloaderApp().mainloop()
