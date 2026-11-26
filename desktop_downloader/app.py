from __future__ import annotations

import copy
import io
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from urllib.parse import urlsplit
import webbrowser
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import tkinter as tk
from PIL import Image
from tkinter import filedialog, messagebox

from app_logging import get_logger, log_path
from diagnostics_window import DiagnosticsWindow
from batch_download import batch_filename, parse_batch_urls
from app_ui import UIMixin
import power
from tray import TrayController
import startup as win_startup
from single_instance import bring_to_front, cleanup_stale_updater_copies, listen_for_show_requests
import theme
import ui_kit as ui
import download_list
import file_download
from title_fix import reconcile_title
from download_queue import DownloadQueue, DownloadRequest
from history_store import HistoryStore, make_history_entry
from media_probe import probe_media
from install_mode import is_installed_mode
from media_editor import MediaEditorWindow
from media_sources import detect_platform, page_fallback_url, platform_name, preferred_page_url
from shared_engine import Cancelled as SharedCancelled, analyze_url as shared_analyze_url, download_from_analysis as shared_download_from_analysis, engine_options
from media_core.engine import is_placeholder_title
from batch_download import parse_batch_urls_detailed
from command_palette import CommandPalette
from download_rules import parse_rules, resolve_target
from extension_server import ExtensionServer, purge_old_cookie_files, write_cookie_file
from failure_dialog import FailureDialog
from hotkey import GlobalHotkey, MOD_ALT, MOD_CONTROL
from options_dialog import OptionsDialog
from update_manager import LATEST_RELEASE_WEB, ReleaseInfo, download_installer_release, download_release, fetch_latest_release, is_newer_version
from version import APP_VERSION

APP_NAME = "Media Downloader"
APP_USER_MODEL_ID = "TeamFahad.MediaDownloader"
MAX_AUTO_RETRIES = 3
AUDIO_FORMATS = {"MP3", "M4A", "FLAC", "OPUS", "WAV"}
# Batch auto-retry never loops on errors that can only be fixed by the user.
# A merge that ffmpeg refuses ("Error opening output files") fails the same way every time.
_NO_AUTO_RETRY = re.compile(
    r"drm.protected|widevine|valid http|no space left|error opening output files", re.IGNORECASE
)
_PERMANENT_ERRORS = re.compile(
    r"unsupported url|private video|sign in|log in|login|members.only|removed|copyright|"
    r"not available in your country|age.restricted|video unavailable|does not exist|no space left",
    re.IGNORECASE,
)
_TRANSIENT_ERRORS = re.compile(
    r"timed out|timeout|connection|reset by peer|temporar|http error 5\d\d|429|"
    r"too many requests|incomplete|eof|ssl|network|unreachable|getaddrinfo|name resolution|"
    r"read error|broken pipe|remote end closed|chunk|retrying",
    re.IGNORECASE,
)


# What the browser extension's "Find qualities" read, kept for a few minutes: when the user
# then picks a quality the download starts at once instead of reading the video again.
_EXT_ANALYSIS_TTL = 8 * 60
_ext_analysis: dict[str, tuple[float, dict[str, Any], dict[str, Any]]] = {}
_ext_analysis_lock = threading.Lock()


def _remember_ext_analysis(url: str, summary: dict[str, Any], info: dict[str, Any]) -> None:
    now = time.monotonic()
    with _ext_analysis_lock:
        for key in [k for k, v in _ext_analysis.items() if now - v[0] > _EXT_ANALYSIS_TTL]:
            _ext_analysis.pop(key, None)
        while len(_ext_analysis) >= 40:
            _ext_analysis.pop(next(iter(_ext_analysis)))
        _ext_analysis[url] = (now, summary, info)


def _recent_ext_analysis(url: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with _ext_analysis_lock:
        hit = _ext_analysis.get(url)
    if not hit or time.monotonic() - hit[0] > _EXT_ANALYSIS_TTL:
        return None
    return copy.deepcopy(hit[1]), copy.deepcopy(hit[2])


def _same_folder(saved: Any, folder: Any) -> bool:
    if not saved or not folder:
        return False
    return os.path.normcase(os.path.normpath(str(saved))) == os.path.normcase(os.path.normpath(str(folder)))


def _widget_alive(widget: Any) -> bool:
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def _link_label(url: str) -> str:
    """A readable name for a link that could not be read yet (instead of "Batch item 1")."""
    parts = urlsplit(url)
    host = (parts.hostname or "link").removeprefix("www.")
    tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if parts.query and len(parts.query) <= 40:
        tail = f"{tail}?{parts.query}" if tail else f"?{parts.query}"
    return f"{host}/{tail}"[:120] if tail else host


def _looks_transient(message: str) -> bool:
    return not _PERMANENT_ERRORS.search(message) and bool(_TRANSIENT_ERRORS.search(message))


def _icon_path() -> Path | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / "assets" / "app_icon.ico", Path(__file__).resolve().parent / "assets" / "app_icon.ico"):
        if candidate.exists():
            return candidate
    return None
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Media Downloader"
CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
CONFIG_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
LEGACY_CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / ("Team" + "Fahad" + "Downloader")
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "settings.json"
EXT_COOKIE_DIR = CONFIG_DIR / "ext_cookies"
EXTENSION_DIR = Path.home() / "Documents" / "MediaDownloader-Browser-Extension"
UPDATE_DIR = Path(os.getenv("LOCALAPPDATA") or CONFIG_DIR) / "MediaDownloader" / "updates"
UPDATE_RESULT_FILE = CONFIG_DIR / "update-result.json"
UPDATE_LOG_FILE = CONFIG_DIR / "update.log"

BG = ui.BG
SURFACE = ui.SURFACE
SURFACE_2 = ui.SURFACE_2
SURFACE_3 = ui.SURFACE_3
BORDER = ui.BORDER
TEXT = ui.TEXT
MUTED = ui.MUTED
CYAN = ui.CYAN
PURPLE = ui.ACCENT
SUCCESS = ui.SUCCESS
WARNING = ui.WARNING
DANGER = ui.DANGER



def _enable_dpi_awareness() -> None:
    """Draw at the real display resolution (no blurry bitmap scaling) on Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _fit_screen(root: tk.Misc, width: int, height: int) -> tuple[int, int]:
    """A size in 96-dpi pixels, scaled for the display and kept inside the screen."""
    w, h = ui.px(width), ui.px(height)
    return min(w, root.winfo_screenwidth() - 40), min(h, root.winfo_screenheight() - 80)


def _dark_title_bar(window: tk.Misc, dark: bool) -> None:
    """Windows 10/11: a dark title bar for the dark theme (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    if os.name != "nt" or not dark:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


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


def is_valid_quality(value: str) -> bool:
    """'Best available' or a height such as 720p (the browser extension may send 540p, 1440p...)."""
    return value == "Best available" or bool(re.fullmatch(r"[1-9]\d{2,3}p", value or ""))


def default_settings() -> dict[str, Any]:
    return {
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "auto_check_updates": True,
        "auto_download_updates": False,
        "auto_analyze_links": True,
        "open_editor_after_download": False,
        "confirm_before_exit": True,
        "close_to_tray": True,
        "capture_dialog": True,
        "start_with_windows": False,
        "default_mode": "Video",
        "video_quality": "720p",
        "audio_format": "MP3",
        "audio_quality": "192",
        "max_concurrent_downloads": 6,
        "skip_existing": True,
        "organize_by_site": False,
        "fast_mode": True,
        "embed_metadata": True,
        "embed_thumbnail": False,
        "subtitles": "Off",
        "subtitle_langs": "en",
        "folder_rules": "",
        "cookies_file": "",
        "cookies_browser": "None",
        "clipboard_hotkey": True,
        "extension_enabled": True,
        "extension_token": "",
        "snooze_version": "",
        "snooze_until": 0,
        "theme": "Light",
        "show_categories": True,
        "show_details": True,
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
    settings["audio_format"] = audio_format if audio_format in AUDIO_FORMATS else "MP3"

    subtitles = str(settings.get("subtitles") or "Off")
    settings["subtitles"] = subtitles if subtitles in {"Off", "Sidecar file", "Embed in video"} else "Off"
    settings["subtitle_langs"] = str(settings.get("subtitle_langs") or "en").strip() or "en"
    browser = str(settings.get("cookies_browser") or "None")
    settings["cookies_browser"] = browser if browser in {"None", "Firefox", "Chrome", "Edge", "Brave"} else "None"
    for key in ("folder_rules", "cookies_file", "extension_token"):
        settings[key] = str(settings.get(key) or "")

    audio_quality = str(settings.get("audio_quality") or "192")
    settings["audio_quality"] = audio_quality if audio_quality in {"320", "256", "192", "128"} else "192"

    try:
        concurrent = int(settings.get("max_concurrent_downloads", 6))
    except (TypeError, ValueError, OverflowError):
        concurrent = 6
    settings["max_concurrent_downloads"] = max(1, min(10, concurrent))

    download_dir = str(settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR).strip()
    settings["download_dir"] = download_dir or str(DEFAULT_DOWNLOAD_DIR)

    for key, fallback in (
        ("auto_check_updates", True),
        ("auto_download_updates", False),
        ("auto_analyze_links", True),
        ("open_editor_after_download", False),
        ("confirm_before_exit", True),
        ("close_to_tray", True),
        ("capture_dialog", True),
        ("start_with_windows", False),
        ("skip_existing", True),
        ("organize_by_site", False),
        ("fast_mode", True),
        ("embed_metadata", True),
        ("embed_thumbnail", False),
        ("clipboard_hotkey", True),
        ("extension_enabled", True),
    ):
        value = settings.get(key, fallback)
        settings[key] = value if isinstance(value, bool) else fallback

    settings["theme"] = theme.normalize_theme(settings.get("theme"))
    for key in ("show_categories", "show_details"):
        settings[key] = settings.get(key) if isinstance(settings.get(key), bool) else True
    if "list_columns" in settings:
        settings["list_columns"] = download_list.normalize_columns(settings.get("list_columns"))
    if not isinstance(settings.get("list_widths"), dict):
        settings.pop("list_widths", None)

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


class DownloaderApp(UIMixin, tk.Tk):
    def __init__(self, native_path: Path | None = None) -> None:
        if os.name == "nt":
            try:  # own taskbar identity so the download icon shows (and pins) instead of python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
            except Exception:
                pass
        _enable_dpi_awareness()
        super().__init__()
        self.native_shell = None
        self.title(APP_NAME)
        self._apply_window_icon()
        self._start_minimized = win_startup.started_minimized()
        if self._start_minimized or native_path is not None:
            self.withdraw()  # launched by Windows at sign-in: go straight to the tray
        self.after(400, self._init_tray)
        # A second launch from the taskbar / Start menu brings this window back from the tray.
        listen_for_show_requests(lambda: self._run_on_ui(self.show_from_tray))
        ui.init(self)  # display scaling, before any size below
        width, height = _fit_screen(self, 1180, 780)
        self.geometry(f"{width}x{height}")
        self.minsize(*_fit_screen(self, 900, 560))
        self.configure(bg=BG)
        self.after(0, lambda: _dark_title_bar(self, theme.IS_DARK))
        self.protocol("WM_DELETE_WINDOW", self._close_app)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_info: dict[str, Any] | None = None
        self.thumbnail_image: Any = None
        self.last_file: Path | None = None
        self.editor_window: MediaEditorWindow | None = None
        self.recorder_window: Any = None
        self.audio_recorder_window: Any = None
        self.diagnostics_window: DiagnosticsWindow | None = None
        self.settings_panel: OptionsDialog | None = None
        self.settings_window: Any = None
        self.task_state = TaskState.IDLE

        self._analysis_counter = 0
        self.analysis_job_id: int | None = None
        self.analysis_cancel_event: threading.Event | None = None
        self.analysis_url = ""
        self.analysis_running = False

        self._quitting = False
        self._update_handoff_started = False
        self.tray: TrayController | None = None
        self._tray_hint_shown = False
        self._tray_status_text = ""
        self._pause_ids: set[str] = set()
        self._reserved_names: dict[str, str] = {}
        self._name_lock = threading.Lock()
        self._notified_done: set[str] = set()
        power.disable_power_throttling()

        self.download_cancel_events: dict[str, threading.Event] = {}
        self._download_progress_lock = threading.Lock()
        self._latest_download_progress: dict[str, dict[str, Any]] = {}
        self._latest_download_metrics: dict[str, dict[str, Any]] = {}
        self._stopping_workers: set[str] = set()   # stopped by force, thread still winding down
        self._forced_cancelled: set[str] = set()
        self._capture_ids: set[str] = set()   # downloads shown in their own window: no notifications
        self._capture_dialogs: list[Any] = []
        self._download_activity_after_id: str | None = None

        self.settings = load_settings()
        self._sync_start_with_windows()
        self._hotkeys: list[GlobalHotkey] = []
        self.extension_server: ExtensionServer | None = None
        if not self.settings.get("extension_token"):
            self.settings["extension_token"] = secrets.token_urlsafe(24)
            save_settings(self.settings)
        self._configure_engine()
        purge_old_cookie_files(EXT_COOKIE_DIR)
        cleanup_stale_updater_copies(UPDATE_DIR)
        self.download_queue = DownloadQueue(max_concurrent=self.settings["max_concurrent_downloads"])
        self.history_store = HistoryStore(HISTORY_FILE)
        self.download_dir = Path(str(self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR)).expanduser()
        self.latest_release: ReleaseInfo | None = None
        self.downloaded_update: Path | None = None
        self.update_checking = False
        self.update_downloading = False
        self.update_cancel_event: threading.Event | None = None

        self.url_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.open_editor_after_var = tk.BooleanVar(value=bool(self.settings.get("open_editor_after_download", False)))
        self.url_auto_after_id: str | None = None
        self.last_analyzed_url = ""
        self.update_expanded = False
        self.mode_var = tk.StringVar(value=str(self.settings.get("default_mode") or "Video"))
        self.video_quality_var = tk.StringVar(value=str(self.settings.get("video_quality") or "720p"))
        self.audio_format_var = tk.StringVar(value=str(self.settings.get("audio_format") or "MP3"))
        self.audio_quality_var = tk.StringVar(value=str(self.settings.get("audio_quality") or "192"))
        self.download_dir_var = tk.StringVar(value=str(self.download_dir))
        self.auto_check_updates_var = tk.BooleanVar(value=bool(self.settings.get("auto_check_updates", True)))
        self.auto_download_updates_var = tk.BooleanVar(value=bool(self.settings.get("auto_download_updates", False)))

        self._center_window()
        self._build_ui()
        if native_path is not None:
            from native_shell import NativeShell

            try:
                self.native_shell = NativeShell(self, native_path)
            except OSError:
                LOGGER.exception("Could not start the native desktop")
                if not self._start_minimized:
                    self.deiconify()
            else:
                self._render_download_activity()
        self._sync_recent_file()
        LOGGER.info("App started version=%s executable=%s", APP_VERSION, sys.executable)
        self.after(120, self._drain_events)
        self.after(900, self._start_integrations)
        self.bind_all("<Control-k>", lambda _e: self.open_command_palette())
        self.bind_all("<Control-K>", lambda _e: self.open_command_palette())
        self.after(700, lambda: self._show_update_result(attempt=0))
        # Nobody has to look for updates: check shortly after start and then every few hours;
        # a new version shows on the toolbar's Updates button (and once in the tray).
        self.after(1500, self._auto_update_check)

    UPDATE_RECHECK_MS = 3 * 60 * 60 * 1000

    def _auto_update_check(self) -> None:
        try:
            if self.auto_check_updates_var.get() and not self.downloaded_update:
                self.check_for_updates(manual=False, inline=True, automatic=True)
        finally:
            self.after(self.UPDATE_RECHECK_MS, self._auto_update_check)

    def _apply_window_icon(self) -> None:
        icon = _icon_path()
        if icon is None or os.name != "nt":
            return

        def apply() -> None:
            try:
                self.iconbitmap(default=str(icon))
                self.iconbitmap(str(icon))
            except Exception:
                pass

        apply()

    # ------------------------------------------------------- tray / background
    def _init_tray(self) -> None:
        if self.tray is not None:
            return
        controller = TrayController(
            icon_path=_icon_path(),
            title=APP_NAME,
            on_show=self.show_from_tray,
            on_pause_all=self.pause_all_downloads,
            on_resume_all=self.resume_all_downloads,
            on_record=self.open_screen_recorder,
            on_quit=self.quit_from_tray,
            run_on_ui=lambda fn: self._run_on_ui(fn),
        )
        self.tray = controller if controller.start() else None
        if self.tray is None and getattr(self, "_start_minimized", False):
            self.show_from_tray()  # no tray icon available: never leave the app invisible

    def _run_on_ui(self, fn: Any) -> None:
        try:
            self.after(0, fn)
        except Exception:
            pass

    def show_from_tray(self) -> None:
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.show(True)
            self._render_download_activity()
            return
        try:
            self.deiconify()
            if self.state() == "iconic":
                self.state("normal")
            self.lift()
            self.focus_force()
        except Exception:
            pass
        try:
            bring_to_front(int(self.wm_frame(), 16))
        except Exception:
            pass

    def hide_to_tray(self) -> None:
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.show(False)
        else:
            self.withdraw()
        if self.tray is not None and not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify("Still running in the tray. Downloads continue in the background.")

    def quit_from_tray(self) -> None:
        self._quitting = True
        self.show_from_tray()
        self._close_app()

    def _after_activity_render(self, snapshots: list[dict[str, Any]], active: int, queued: int) -> None:
        power.keep_system_awake(active > 0)
        paused = sum(1 for item in snapshots if item.get("status") == "paused")
        parts = []
        if active:
            parts.append(f"{active} downloading")
        if queued:
            parts.append(f"{queued} queued")
        if paused:
            parts.append(f"{paused} paused")
        text = " • ".join(parts) or "Idle"
        if self.tray is not None and text != self._tray_status_text:
            self._tray_status_text = text
            self.tray.set_status(text)
        try:
            shell = getattr(self, "native_shell", None)
            hidden = not shell.visible if shell is not None else self.state() in {"iconic", "withdrawn"}
        except Exception:
            hidden = False
        for item in snapshots:
            if item.get("status") == "completed" and item["id"] not in self._notified_done:
                self._notified_done.add(item["id"])
                if hidden and self.tray is not None and item["id"] not in self._capture_ids:
                    self.tray.notify(str(item.get("name") or "Download"), "Download complete")

    def _close_app(self, *, for_update: bool = False) -> None:
        if (
            not for_update
            and not self._quitting
            and self.tray is not None
            and bool(self.settings.get("close_to_tray", True))
        ):
            self.hide_to_tray()
            return
        active_downloads = self.download_queue.queued_count() + self.download_queue.running_count()
        active = self.analysis_running or self.update_downloading or active_downloads > 0
        if active:
            should_confirm = not for_update and bool(self.settings.get("confirm_before_exit", True))
            if should_confirm and not messagebox.askyesno(
                APP_NAME,
                "Tasks are still running. Cancel them and close Media Downloader?",
                parent=self,
            ):
                self._quitting = False
                return

        self._quitting = True
        if self.analysis_cancel_event is not None:
            self.analysis_cancel_event.set()
        for event in list(self.download_cancel_events.values()):
            event.set()
        if self.update_cancel_event is not None:
            self.update_cancel_event.set()

        LOGGER.info(
            "App closing analysis_running=%s active_downloads=%s update_downloading=%s",
            self.analysis_running,
            active_downloads,
            self.update_downloading,
        )
        power.keep_system_awake(False)
        self._stop_integrations()
        if self.tray is not None:
            self.tray.stop()
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.close()
        self.destroy()

    def _center_window(self) -> None:
        self.update_idletasks()
        width, height = _fit_screen(self, 1180, 780)
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

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
        self._cancel_auto_analyze()
        url = self.url_var.get().strip()

        if self.analysis_running:
            if url != self.analysis_url:
                self.cancel_analysis(silent=True)
            else:
                # Ignore duplicate KeyRelease/programmatic-change events for the
                # same URL while its worker is already running.
                return

        if url != self.last_analyzed_url:
            self.current_info = None
            self.last_analyzed_url = ""
            self.name_var.set("")
            self.download_button.configure(state="disabled")
            self.title_label.configure(
                text="Ready to analyze this link" if url else "Analyze media to see its details here"
            )
            self.meta_label.configure(
                text="Media details will load automatically."
                if url
                else "Title, creator, platform and duration will appear after analysis."
            )
            valid = bool(detect_platform(url))
            self.media_badge.configure(
                text="LINK READY" if valid else "WAITING FOR LINK",
                fg_color=SURFACE_2,
                text_color=CYAN if valid else MUTED,
            )
            self.media_action_hint.configure(
                text="Analyzing will not interrupt downloads already running."
                if valid
                else "Analyze a link first, then add it to the download list."
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
        if not detect_platform(url) or url == self.last_analyzed_url:
            return
        self.analyze_media()

    def _begin_analysis(self, url: str) -> tuple[int, threading.Event]:
        if self.analysis_running:
            self.cancel_analysis(silent=True)
        self._analysis_counter += 1
        job_id = self._analysis_counter
        cancel_event = threading.Event()
        self.analysis_job_id = job_id
        self.analysis_cancel_event = cancel_event
        self.analysis_url = url
        self.analysis_running = True
        self.task_state = TaskState.ANALYZING
        self.cancel_analysis_button.configure(state="normal", text="Cancel analysis")
        self.read_button.configure(text="Analyzing…", state="disabled")
        LOGGER.info("Analysis %s started url=%s", job_id, url)
        return job_id, cancel_event

    def _is_current_analysis(self, job_id: int) -> bool:
        return self.analysis_job_id == job_id

    def _finish_analysis(self, job_id: int) -> None:
        if not self._is_current_analysis(job_id):
            return
        self.analysis_job_id = None
        self.analysis_cancel_event = None
        self.analysis_url = ""
        self.analysis_running = False
        self.cancel_analysis_button.configure(state="disabled", text="Cancel analysis")
        self.read_button.configure(text="Analyze", state="normal")

    def _put_analysis_event(self, kind: str, job_id: int, data: Any = None) -> None:
        self.events.put((kind, {"job_id": job_id, "data": data}))

    def cancel_analysis(self, silent: bool = False) -> None:
        event = self.analysis_cancel_event
        job_id = self.analysis_job_id
        if event is None or job_id is None:
            return
        event.set()

        # Invalidate immediately. yt-dlp may be inside a blocking network call,
        # so the UI must not wait for that call before the user can analyze again.
        self.analysis_job_id = None
        self.analysis_cancel_event = None
        self.analysis_url = ""
        self.analysis_running = False
        self.cancel_analysis_button.configure(state="disabled", text="Cancel analysis")
        self.read_button.configure(text="Analyze", state="normal")
        self.download_button.configure(state="disabled")
        if not silent:
            self.task_state = TaskState.CANCELLED
            self.media_badge.configure(text="ANALYSIS CANCELLED", fg_color=ui.WARNING_SOFT, text_color=WARNING)
            self._set_status("Analysis cancelled. Paste or analyze another link.", "cancelled")
        LOGGER.info("Analysis %s cancellation requested", job_id)

    def _analysis_watchdog(self, job_id: int, url: str) -> None:
        if not self._is_current_analysis(job_id):
            return
        if self.url_var.get().strip() != url:
            return

        event = self.analysis_cancel_event
        if event is not None:
            event.set()

        self.current_info = None
        self.last_analyzed_url = ""
        self.download_button.configure(state="disabled")
        self.title_label.configure(text="Analysis timed out")
        self.meta_label.configure(text="The website did not return media information in time.")
        self.media_badge.configure(text="ANALYSIS TIMEOUT", fg_color=ui.DANGER_SOFT, text_color=DANGER)
        self.media_action_hint.configure(text="Press Analyze to retry, or paste another link.")
        self.task_state = TaskState.ERROR
        self._set_status("Analysis timed out after 150 seconds", "error")
        self._finish_analysis(job_id)
        LOGGER.warning("Analysis %s timed out url=%s", job_id, url)

    def analyze_media(self) -> None:
        self._cancel_auto_analyze()
        url = self.url_var.get().strip()
        platform = detect_platform(url)
        if not platform:
            messagebox.showerror(APP_NAME, "Please paste a valid http:// or https:// media URL.")
            return

        if self.analysis_running:
            if url == self.analysis_url:
                self._set_status("This link is already being analyzed.", "working")
                return
            self.cancel_analysis(silent=True)

        job_id, cancel_event = self._begin_analysis(url)
        self.current_info = None
        self.last_analyzed_url = ""
        self.download_button.configure(state="disabled")
        self.title_label.configure(text=f"Analyzing {platform_name(url)} media…")
        self.meta_label.configure(text="Trying compatible connection paths…")
        self.media_action_hint.configure(text="You can cancel this analysis or keep existing downloads running.")
        self._set_status(f"Analyzing {platform_name(url)}…", "working")
        self.media_badge.configure(
            text=f"{platform_name(url).upper()} • ANALYZING",
            fg_color=ui.INFO_SOFT,
            text_color=CYAN,
        )

        # Thumbnail cleanup is cosmetic. A Tk image error must never prevent the
        # extraction worker from starting for the next link.
        try:
            self._apply_thumbnail(None)
        except Exception:
            LOGGER.exception("Thumbnail reset failed before analysis %s", job_id)

        threading.Thread(
            target=self._analyze_media_worker,
            args=(job_id, cancel_event, url),
            daemon=True,
            name=f"media-analysis-{job_id}",
        ).start()
        self.after(150_000, lambda jid=job_id, source=url: self._analysis_watchdog(jid, source))

    def _file_analysis(self, url: str, name: str = "") -> dict[str, Any]:
        """Add URL with a link to a plain file (zip, exe, pdf...): no video to read, just save it."""
        name = name or file_download.name_from_url(url) or "download"
        return {
            "source_url": url,
            "title": name,
            "channel": urlsplit(url).hostname or "File",
            "duration": "--:--",
            "views": None,
            "platform": "file",
            "info": {"direct_file": True, "title": name},
            "thumbnail": None,
        }

    def _analyze_media_worker(self, job_id: int, cancel_event: threading.Event, url: str) -> None:
        if file_download.looks_like_file(url):
            self._put_analysis_event("analysis_info", job_id, self._file_analysis(url))
            self._put_analysis_event("analysis_finished", job_id)
            return
        try:
            summary, info = shared_analyze_url(
                url,
                cancel_event=cancel_event,
                on_status=lambda text: self._put_analysis_event(
                    "analysis_status",
                    job_id,
                    f"{text}…" if text and not str(text).endswith("…") else str(text),
                ),
            )
            if file_download.is_plain_file_info(info) and not cancel_event.is_set():
                found = file_download.probe_file(url) or {}
                self._put_analysis_event("analysis_info", job_id, self._file_analysis(url, found.get("name", "")))
                return

            thumb_bytes = None
            thumbnail_url = str(summary.get("thumbnail") or "")
            if thumbnail_url and not cancel_event.is_set():
                try:
                    request = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        thumb_bytes = response.read(2_500_000)
                except Exception:
                    thumb_bytes = None

            if cancel_event.is_set():
                self._put_analysis_event("analysis_cancelled", job_id, "Analysis cancelled.")
                return

            self._put_analysis_event(
                "analysis_info",
                job_id,
                {
                    "source_url": url,
                    "title": str(summary.get("title") or "Media"),
                    "channel": str(summary.get("creator") or "Creator"),
                    "duration": format_duration(summary.get("duration")),
                    "views": info.get("view_count"),
                    "platform": detect_platform(url) or str(info.get("extractor_key") or "media").lower(),
                    "info": info,
                    "thumbnail": thumb_bytes,
                },
            )
        except SharedCancelled:
            self._put_analysis_event("analysis_cancelled", job_id, "Analysis cancelled.")
        except Exception as exc:
            found = None if cancel_event.is_set() else file_download.probe_file(url)
            if found:
                LOGGER.info("Analysis %s: link is a plain file", job_id)
                self._put_analysis_event("analysis_info", job_id, self._file_analysis(url, found["name"]))
                return
            LOGGER.exception("Analysis %s failed", job_id)
            self._put_analysis_event(
                "analysis_error",
                job_id,
                "Could not extract media from this URL after trying the available connection paths. "
                "The page may require login, use DRM, block automated access, or use a player yt-dlp does not support yet.\n\n"
                + str(exc),
            )
        finally:
            self._put_analysis_event("analysis_finished", job_id)

    def download(self) -> None:
        self._start_download(edit_after_download=bool(self.open_editor_after_var.get()))

    def _available_download_name(self, base_name: str) -> str:
        base = safe_filename(base_name, "media_download")
        used = {
            str(item.get("name") or "")
            for item in self.download_queue.snapshot()
            if str(item.get("status") or "") in {"queued", "running", "cancelling"}
        }

        def exists(name: str) -> bool:
            if name in used:
                return True
            try:
                return any(path.is_file() and path.stem == name for path in self.download_dir.iterdir())
            except OSError:
                return False

        if not exists(base):
            return base
        for index in range(2, 1000):
            candidate = f"{base} ({index})"
            if not exists(candidate):
                return candidate
        return f"{base} {int(time.time())}"

    def open_batch_downloads(self) -> None:
        self.show_page("batch")

    def _enqueue_batch_downloads(
        self,
        text: str,
        options: dict[str, str],
        extra: dict[str, Any] | None = None,
        *,
        target_dir_override: Path | None = None,
        hold: bool = False,
        ids_out: list[str] | None = None,
    ) -> tuple[str, int, int]:
        urls, duplicates = parse_batch_urls(text)
        mode = str(options.get("mode") or "Video")
        quality = str(options.get("video_quality") or "720p")
        audio_format = str(options.get("audio_format") or "MP3")
        bitrate = str(options.get("audio_quality") or "192")
        if mode not in {"Video", "Audio"}:
            raise ValueError("Select Video or Audio.")
        if not is_valid_quality(quality):
            raise ValueError("Invalid video quality.")
        if audio_format not in AUDIO_FORMATS or bitrate not in {"128", "192", "256", "320"}:
            raise ValueError("Invalid audio output settings.")

        self.download_dir.mkdir(parents=True, exist_ok=True)
        batch_id = uuid.uuid4().hex
        payloads = []
        rules = parse_rules(str(self.settings.get("folder_rules") or ""))
        # A stream sent by the browser extension lives on a CDN; website folder rules and the
        # history's "website" use the page it was playing on.
        page_url = str((extra or {}).get("page_url") or "") if len(urls) == 1 else ""
        if not page_url.startswith(("http://", "https://")):
            page_url = ""
        for index, url in enumerate(urls, 1):
            site_url = page_url or url
            target_dir, url_quality = resolve_target(
                site_url,
                rules=rules,
                default_dir=self.download_dir,
                default_quality=quality,
                organize_by_site=bool(self.settings.get("organize_by_site", False)),
                platform=platform_name(site_url),
            )
            if target_dir_override is not None:
                target_dir = target_dir_override
            request_settings = {
                **(extra or {}),
                "batch_id": batch_id,
                "batch_index": index,
                "mode": mode,
                "video_quality": url_quality,
                "audio_format": audio_format.lower(),
                "audio_quality": bitrate,
                "source_url": url,
                "page_url": site_url,
                "history_title": str((extra or {}).get("title_hint") or "") or _link_label(url),
                "history_platform": platform_name(site_url),
                "history_quality": url_quality if mode == "Video" else f"{audio_format} {bitrate} kbps",
                "history_duration": 0,
            }
            file_name = (
                str((extra or {}).get("file_name") or (extra or {}).get("custom_title") or "")
                if len(urls) == 1 else ""
            )
            payloads.append({
                "url": url,
                "name": file_name or _link_label(url),
                "batch_id": batch_id,
                "batch_index": index,
                "download_dir": str(target_dir),
                "request_settings": request_settings,
                "cached_info": None,
            })
        requests = self.download_queue.enqueue_batch(payloads)
        if ids_out is not None:
            ids_out.extend(request.id for request in requests)
        if hold:
            # "Download later": waits in the list, paused, until the user resumes it.
            for request in requests:
                self.download_queue.pause(request.id)
        LOGGER.info(
            "Desktop batch queued batch=%s links=%s duplicates_skipped=%s",
            batch_id, len(payloads), duplicates
        )
        self._set_status(f"Queued {len(payloads)} batch downloads", "ready")
        self._schedule_download_activity_refresh(40)
        self._start_available_downloads()
        return batch_id, len(payloads), duplicates

    def _start_download(self, edit_after_download: bool) -> None:
        url = self.url_var.get().strip()
        platform = detect_platform(url)
        if not platform:
            messagebox.showerror(APP_NAME, "Please paste a valid http:// or https:// media URL.")
            return
        if not isinstance(self.current_info, dict) or self.last_analyzed_url != url:
            self._set_status("Analyze this link before downloading it.", "error")
            messagebox.showinfo(APP_NAME, "Analyze this link first, then press Download.")
            return

        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.exception("Could not create download directory")
            messagebox.showerror(APP_NAME, f"Could not use the selected download folder.\n\n{exc}")
            return

        name = self._available_download_name(self.name_var.get())
        info = dict(self.current_info)
        target_dir, rule_quality = resolve_target(
            url,
            rules=parse_rules(str(self.settings.get("folder_rules") or "")),
            default_dir=self.download_dir,
            default_quality=self.video_quality_var.get(),
            organize_by_site=bool(self.settings.get("organize_by_site", False)),
            platform=platform_name(url),
        )
        try:
            history_duration = max(0, int(float(info.get("duration") or 0)))
        except (TypeError, ValueError):
            history_duration = 0

        mode = self.mode_var.get()
        audio_format = self.audio_format_var.get()
        audio_quality = self.audio_quality_var.get()
        video_quality = rule_quality

        request_settings = {
            "mode": mode,
            "video_quality": video_quality,
            "audio_format": audio_format.lower(),
            "audio_quality": audio_quality,
            "history_title": str(info.get("title") or self.name_var.get() or name),
            "history_creator": str(info.get("channel") or info.get("uploader") or info.get("creator") or ""),
            "history_platform": platform_name(url),
            "history_duration": history_duration,
            "history_quality": (
                str(video_quality)
                if mode == "Video"
                else f"{audio_format.upper()} {audio_quality} kbps"
            ),
            "source_url": url,
            "page_url": url,
        }
        if info.get("direct_file"):
            # A plain file: keep the name the user typed, with the link's file extension.
            ext = os.path.splitext(str(info.get("title") or ""))[1]
            request_settings.update({"direct": True, "history_quality": "",
                                     "file_name": name if name.lower().endswith(ext.lower()) else name + ext})

        request = self.download_queue.enqueue(
            {
                "url": url,
                "name": name,
                "download_dir": str(target_dir),
                "request_settings": request_settings,
                "cached_info": copy.deepcopy(info),
            },
            edit_after_download=bool(edit_after_download),
        )
        LOGGER.info("Queued link download request=%s url=%s", request.id, url)
        self._set_status(f"Added to downloads: {name}", "ready")
        self.media_action_hint.configure(text="Added to downloads. Paste another link whenever you want.")
        self._start_available_downloads()
        self._after_link_queued(request.id, name)

    def _put_download_event(self, kind: str, request_id: str, data: Any = None) -> None:
        if kind == "download_progress":
            with self._download_progress_lock:
                self._latest_download_progress[request_id] = dict(data or {})
            return
        if kind == "download_metrics":
            # Speed, size and time left for the list's columns; read on the next refresh.
            with self._download_progress_lock:
                self._latest_download_metrics[request_id] = dict(data or {})
            return
        self.events.put((kind, {"request_id": request_id, "data": data}))

    def _download_worker(
        self,
        request_id: str,
        cancel_event: threading.Event,
        url: str,
        name: str,
        download_dir: Path,
        request_settings: dict[str, Any],
        cached_info: dict[str, Any] | None = None,
    ) -> None:
        engine_options.set_request_context(
            cookiefile=str(request_settings.get("cookiefile") or ""),
            referer=str(request_settings.get("referer") or ""),
            headers=dict(request_settings.get("http_headers") or {}),
            skip_existing=False if request_settings.get("force_redownload") else None,
        )
        direct = bool(request_settings.get("direct"))
        try:
            if request_settings.get("batch_id") and not direct:
                if cancel_event.is_set():
                    raise SharedCancelled("Batch item cancelled before analysis.")
                self._put_download_event(
                    "download_status", request_id, "Analyzing batch link…"
                )
                def analyze(link: str) -> tuple[dict[str, Any], dict[str, Any]]:
                    recent = _recent_ext_analysis(link)
                    if recent is not None:
                        return recent
                    return shared_analyze_url(
                        link,
                        cancel_event=cancel_event,
                        on_status=lambda text: self._put_download_event(
                            "download_status", request_id, f"Analyze: {text}"
                        ),
                    )

                fallback_url = str(request_settings.get("fallback_url") or "")

                def plain_file() -> bool:
                    found = file_download.probe_file(
                        url,
                        headers=dict(request_settings.get("http_headers") or {}),
                        cookiefile=str(request_settings.get("cookiefile") or ""),
                        referer=str(request_settings.get("referer") or ""),
                    )
                    if found and not request_settings.get("file_name"):
                        request_settings["file_name"] = found["name"]
                    return found is not None

                try:
                    summary, extracted_info = analyze(url)
                except SharedCancelled:
                    raise
                except Exception as exc:
                    LOGGER.info("Analysis failed request=%s url=%s: %s", request_id, url, " ".join(str(exc).split())[:600])
                    if fallback_url and fallback_url != url and not cancel_event.is_set():
                        # The page could not be read: use the stream the browser captured instead,
                        # or the captured stream was refused: read the page it was playing on.
                        # (Kept in the settings, so a manual Retry gets the same second chance.)
                        LOGGER.info("Analysis failed request=%s, trying fallback %s", request_id, fallback_url)
                        url = fallback_url
                        summary, extracted_info = analyze(url)
                    elif not cancel_event.is_set() and plain_file():
                        # Not a video page but a file (download.php?id=..., an attachment...): save it as-is.
                        LOGGER.info("Link is a plain file, downloading it directly request=%s", request_id)
                        direct = True
                        summary, extracted_info = {}, {}
                    else:
                        raise
                if not direct and file_download.is_plain_file_info(extracted_info) and plain_file():
                    direct = True
                if cancel_event.is_set():
                    raise SharedCancelled("Batch item cancelled after analysis.")
            if request_settings.get("batch_id") and not direct:
                cached_info = extracted_info
                # Streams captured by the browser extension carry the page title; a bare
                # .m3u8 would otherwise be saved as "master" or "index".
                title_hint = str(request_settings.get("title_hint") or "").strip()
                custom_title = str(request_settings.get("custom_title") or "").strip()
                if custom_title:
                    # The name the user confirmed in the download window always wins.
                    summary = {**summary, "title": custom_title}
                # "Untitled" (a site that hides its title) never replaces a real title.
                elif title_hint and not is_placeholder_title(title_hint):
                    summary = {**summary, "title": title_hint}
                name = self._reserve_name(
                    request_id,
                    batch_filename(
                        str(request_settings["batch_id"]),
                        int(request_settings.get("batch_index") or 0),
                        str(summary.get("title") or "media"),
                    ),
                )
                self._put_download_event("download_name", request_id, name)
                request_settings["history_title"] = str(summary.get("title") or name)
                request_settings["history_creator"] = str(summary.get("creator") or "")
                request_settings["history_platform"] = str(
                    summary.get("platform") or platform_name(url)
                )
                request_settings["history_duration"] = int(
                    float(summary.get("duration") or 0)
                )
            if direct:
                suggested = str(request_settings.get("file_name") or "") or file_download.name_from_url(url)
                stem, ext = os.path.splitext(file_download.clean_filename(suggested))
                name = self._reserve_name(request_id, stem) + ext
                self._put_download_event("download_name", request_id, name)
                request_settings["history_title"] = name
                request_settings["history_quality"] = ""
            attempt = 0
            while True:
                attempt += 1
                try:
                    if direct:
                        final_path = file_download.direct_download(
                            url=url,
                            download_dir=download_dir,
                            filename=name,
                            cancel_event=cancel_event,
                            headers=dict(request_settings.get("http_headers") or {}),
                            cookiefile=str(request_settings.get("cookiefile") or ""),
                            referer=str(request_settings.get("referer") or ""),
                            on_status=lambda text: self._put_download_event("download_status", request_id, f"{text}…"),
                            on_progress=lambda value, detail: self._put_download_event(
                                "download_progress", request_id,
                                {"percent": float(value) * 100.0, "detail": str(detail or "")},
                            ),
                            on_metrics=lambda metrics: self._put_download_event("download_metrics", request_id, metrics),
                        )
                        break
                    final_path = shared_download_from_analysis(
                        url=url,
                        cached_info=dict(cached_info or {}),
                        download_dir=download_dir,
                        filename=name,
                        mode=str(request_settings.get("mode") or "Video"),
                        video_quality=str(request_settings.get("video_quality") or "Best available"),
                        audio_format=str(request_settings.get("audio_format") or "mp3"),
                        audio_quality=str(request_settings.get("audio_quality") or "192"),
                        cancel_event=cancel_event,
                        keep_partial=lambda rid=request_id: rid in self._pause_ids,
                        on_status=lambda text: self._put_download_event(
                            "download_status",
                            request_id,
                            f"{text}…" if text and not str(text).endswith("…") else str(text),
                        ),
                        on_progress=lambda value, detail: self._put_download_event(
                            "download_progress",
                            request_id,
                            {"percent": float(value) * 100.0, "detail": str(detail or "")},
                        ),
                        on_metrics=lambda metrics: self._put_download_event("download_metrics", request_id, metrics),
                    )
                    break
                except SharedCancelled:
                    raise
                except Exception as exc:
                    if cancel_event.is_set() or attempt > MAX_AUTO_RETRIES or not _looks_transient(str(exc)):
                        raise
                    LOGGER.warning("Transient failure request=%s attempt=%s: %s", request_id, attempt, exc)
                    self._put_download_event(
                        "download_status",
                        request_id,
                        f"Connection problem — retrying ({attempt}/{MAX_AUTO_RETRIES})…",
                    )
                    if cancel_event.wait(min(3 * attempt, 15)):
                        raise SharedCancelled("Download cancelled while waiting to retry.")

            media_file = download_list.category_for(Path(final_path).name) in {"video", "audio"}
            probe = probe_media(Path(final_path)) if media_file else {}
            self._put_download_event(
                "download_done",
                request_id,
                {
                    "probe": probe,
                    "attempts": attempt,
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
        except SharedCancelled:
            self._put_download_event("download_cancelled", request_id, "Download cancelled.")
        except Exception as exc:
            if cancel_event.is_set():
                self._put_download_event("download_cancelled", request_id, "Download cancelled.")
            else:
                LOGGER.exception("Download request %s failed", request_id)
                self._put_download_event("download_error", request_id, str(exc))
        finally:
            engine_options.clear_request_context()
            self._put_download_event("download_finished", request_id)

    def _drain_events(self) -> None:
        with self._download_progress_lock:
            progress_items = list(self._latest_download_progress.items())
            self._latest_download_progress.clear()

        if progress_items:
            for request_id, detail in progress_items:
                percent = max(0.0, min(100.0, float(detail.get("percent") or 0.0)))
                progress_detail = str(detail.get("detail") or "Downloading…")
                self.download_queue.update_progress(
                    request_id,
                    percent / 100.0,
                    progress_detail,
                )
            self._schedule_download_activity_refresh(80)

        processed = 0
        started = time.monotonic()
        try:
            while processed < 100 and (time.monotonic() - started) < 0.014:
                kind, payload = self.events.get_nowait()
                processed += 1

                if kind.startswith("analysis_"):
                    if not isinstance(payload, dict):
                        continue
                    job_id = int(payload.get("job_id") or -1)
                    if not self._is_current_analysis(job_id):
                        continue
                    data = payload.get("data")

                    if kind == "analysis_info":
                        media = dict(data or {})
                        source_url = str(media.get("source_url") or "")
                        if source_url != self.url_var.get().strip():
                            self._finish_analysis(job_id)
                            continue

                        self.current_info = media.get("info")
                        self.last_analyzed_url = source_url
                        self.name_var.set(safe_filename(str(media.get("title") or "Media")))
                        self.title_label.configure(text=str(media.get("title") or "Media"))
                        platform_label = platform_name(source_url)
                        self.meta_label.configure(
                            text=f"{media.get('channel') or 'Creator'}  •  {platform_label}  •  {media.get('duration') or '--:--'}"
                        )
                        self.media_badge.configure(
                            text=f"{platform_label.upper()} • READY",
                            fg_color=ui.SUCCESS_SOFT,
                            text_color=SUCCESS,
                        )
                        self.media_action_hint.configure(
                            text="Ready. Download this file or paste another link after adding it to the list."
                        )
                        self._apply_thumbnail(media.get("thumbnail"))
                        self.download_button.configure(state="normal")
                        if isinstance(self.current_info, dict) and self.current_info.get("direct_file"):
                            self.download_button.configure(text="Download file")
                        self.task_state = TaskState.READY
                        self._set_status(f"{platform_label} media information loaded", "ready")
                        self._finish_analysis(job_id)

                    elif kind == "analysis_status":
                        self._set_status(str(data or "Analyzing…"), "working")

                    elif kind == "analysis_error":
                        self.current_info = None
                        self.last_analyzed_url = ""
                        self.download_button.configure(state="disabled")
                        self.task_state = TaskState.ERROR
                        self.title_label.configure(text="Could not analyze this link")
                        self.meta_label.configure(text="All compatible extraction paths failed.")
                        self.media_badge.configure(text="ANALYSIS FAILED", fg_color=ui.DANGER_SOFT, text_color=DANGER)
                        self.media_action_hint.configure(text="Try Re-analyze or paste another public link.")
                        self._set_status("Analysis failed", "error")
                        self._finish_analysis(job_id)
                        messagebox.showerror(APP_NAME, str(data))

                    elif kind == "analysis_cancelled":
                        self.current_info = None
                        self.last_analyzed_url = ""
                        self.download_button.configure(state="disabled")
                        self.task_state = TaskState.CANCELLED
                        self.title_label.configure(text="Analysis cancelled")
                        self.meta_label.configure(text="Paste another link or press Analyze to try again.")
                        self.media_badge.configure(text="ANALYSIS CANCELLED", fg_color=ui.WARNING_SOFT, text_color=WARNING)
                        self._set_status(str(data or "Analysis cancelled."), "cancelled")
                        self._finish_analysis(job_id)

                    elif kind == "analysis_finished":
                        self._finish_analysis(job_id)

                    continue

                if kind == "history_loaded":
                    if isinstance(payload, dict):
                        self._history_loaded(payload)
                    continue

                if kind == "external_download":
                    if isinstance(payload, dict):
                        self._handle_external_download(payload)
                    continue
                if kind == "hotkey_clipboard":
                    if isinstance(payload, dict):
                        self._download_clipboard_link(str(payload.get("mode") or "Video"))
                    continue

                if kind.startswith("download_"):
                    if not isinstance(payload, dict):
                        continue
                    request_id = str(payload.get("request_id") or "")
                    data = payload.get("data")
                    request_state = self.download_queue.get(request_id)

                    if request_id in self._stopping_workers and kind != "download_finished":
                        # This worker was stopped by force (cancel/pause took too long): whatever it
                        # still reports is stale. A file it managed to finish after a cancel goes away.
                        if kind == "download_done" and request_id in self._forced_cancelled:
                            try:
                                Path(str((data or {}).get("path") or "")).unlink(missing_ok=True)
                            except OSError:
                                pass
                        continue

                    if kind == "download_status":
                        self.download_queue.update_detail(request_id, str(data or "Working…"))
                        self._schedule_download_activity_refresh(80)

                    elif kind == "download_name":
                        self.download_queue.update_name(request_id, str(data or "Media"))
                        self._schedule_download_activity_refresh(80)

                    elif kind == "download_done":
                        detail = dict(data or {})
                        completed_path = Path(str(detail.get("path") or ""))
                        edit_after = bool((request_state or {}).get("edit_after_download"))
                        self.download_queue.complete(request_id, detail=completed_path.name, path=str(completed_path))
                        cookie_path = str(((request_state or {}).get("cookiefile")) or "")
                        if cookie_path:
                            try:
                                Path(cookie_path).unlink(missing_ok=True)
                            except OSError:
                                pass
                        self.last_file = completed_path
                        self.task_state = TaskState.DOWNLOADED
                        self._set_status(f"Download completed: {completed_path.name}", "success")

                        self._record_history(request_id, "completed", path=completed_path, detail=detail)

                        self._sync_recent_file()

                        if edit_after:
                            existing = self.editor_window
                            can_open = True
                            if existing is not None:
                                try:
                                    can_open = not existing.winfo_exists()
                                except Exception:
                                    can_open = True
                            if can_open:
                                self.after(180, lambda path=completed_path: self.open_editor(path))

                        self._schedule_download_activity_refresh(30)

                    elif kind == "download_error":
                        delay = (
                            None
                            if _NO_AUTO_RETRY.search(str(data or ""))
                            else self.download_queue.schedule_auto_retry(request_id, str(data or ""))
                        )
                        if delay is not None:
                            LOGGER.info("Batch item failed, auto-retry in %ss request=%s: %s", delay, request_id, data)
                            self.after(int(delay * 1000), lambda rid=request_id: self._release_auto_retry(rid))
                            self._set_status("A batch link failed — retrying automatically.", "ready")
                        else:
                            self.download_queue.fail(request_id, str(data or "Download failed"))
                            self._record_history(request_id, "failed", error=str(data or "Download failed"))
                            self.task_state = TaskState.ERROR
                            self._set_status("A download failed. See Download Activity for details.", "error")
                        self._schedule_download_activity_refresh(30)

                    elif kind == "download_cancelled":
                        if request_id in self._pause_ids:
                            self._pause_ids.discard(request_id)
                            self.download_queue.mark_paused(request_id)
                            self._set_status("Download paused", "ready")
                        else:
                            self.download_queue.cancel(request_id)
                            self._record_history(request_id, "cancelled")
                            self._set_status("Download cancelled", "cancelled")
                        self._schedule_download_activity_refresh(30)

                    elif kind == "download_finished":
                        self._stopping_workers.discard(request_id)
                        self._forced_cancelled.discard(request_id)
                        self.download_cancel_events.pop(request_id, None)
                        with self._download_progress_lock:
                            self._latest_download_metrics.pop(request_id, None)
                        snapshot = self.download_queue.get(request_id)
                        if not snapshot or snapshot.get("status") != "paused":
                            with self._name_lock:
                                self._reserved_names.pop(request_id, None)
                        self._start_available_downloads()
                        self._schedule_download_activity_refresh(30)

                    continue

                if kind == "update_available":
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
            self.after(40, self._drain_events)

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
            self.save_location_label.configure(text=f"Saving to  {self.download_dir}")

    def reset_download_folder(self) -> None:
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        self.download_dir_var.set(str(self.download_dir))
        self.settings["download_dir"] = str(self.download_dir)
        save_settings(self.settings)
        self.save_location_label.configure(text=f"Saving to  {self.download_dir}")

    def open_download_folder(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(self.download_dir))
        else:
            subprocess.Popen(["xdg-open", str(self.download_dir)])

    def open_editor(self, source_path: Path | None = None) -> None:
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

    def open_screen_recorder(self) -> None:
        """Bandicam-style screen recorder (full screen or an area, sound, webcam, hotkeys)."""
        window = self.recorder_window
        try:
            if window is not None and window.winfo_exists():
                window.show()
                return
        except Exception:
            self.recorder_window = None
        try:
            from screen_recorder import ScreenRecorderWindow

            stored = self.settings.get("recorder")
            self.recorder_window = ScreenRecorderWindow(
                self,
                output_dir=self.download_dir / "Recordings",
                settings=stored if isinstance(stored, dict) else None,
                save_settings=self._save_recorder_settings,
                open_in_editor=lambda path: self.open_editor(path),
            )
            self.recorder_window.bind("<Destroy>", self._recorder_destroyed, add="+")
            LOGGER.info("Screen recorder opened")
        except Exception as exc:
            LOGGER.exception("Could not open the screen recorder")
            self.recorder_window = None
            messagebox.showerror(APP_NAME, f"Could not open the screen recorder.\n\n{exc}")

    def open_audio_recorder(self) -> None:
        """Audio-only recorder: the other side of a call or meeting plus the microphone."""
        window = self.audio_recorder_window
        try:
            if window is not None and window.winfo_exists():
                window.show()
                return
        except Exception:
            self.audio_recorder_window = None
        try:
            from audio_recorder import AudioRecorderWindow

            stored = self.settings.get("audio_recorder")
            self.audio_recorder_window = AudioRecorderWindow(
                self,
                output_dir=self.download_dir / "Recordings",
                settings=stored if isinstance(stored, dict) else None,
                save_settings=self._save_audio_recorder_settings,
            )
            self.audio_recorder_window.bind("<Destroy>", self._audio_recorder_destroyed, add="+")
            LOGGER.info("Audio recorder opened")
        except Exception as exc:
            LOGGER.exception("Could not open the audio recorder")
            self.audio_recorder_window = None
            messagebox.showerror(APP_NAME, f"Could not open the audio recorder.\n\n{exc}")

    def _save_audio_recorder_settings(self, values: dict[str, Any]) -> None:
        self.settings["audio_recorder"] = values
        save_settings(self.settings)

    def _audio_recorder_destroyed(self, event: Any) -> None:
        if event.widget is self.audio_recorder_window:
            self.audio_recorder_window = None

    def _save_recorder_settings(self, values: dict[str, Any]) -> None:
        self.settings["recorder"] = values
        save_settings(self.settings)

    def _recorder_destroyed(self, event: Any) -> None:
        if event.widget is self.recorder_window:
            self.recorder_window = None

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
        if self.analysis_running:
            self.cancel_analysis(silent=True)

        self.last_analyzed_url = ""
        self.open_editor_after_var.set(bool(self.settings.get("open_editor_after_download", False)))
        self.task_state = TaskState.IDLE
        self.url_var.set("")
        self.name_var.set("")
        self.current_info = None
        self.download_button.configure(state="disabled")
        self.title_label.configure(text="Analyze media to see its details here")
        self.meta_label.configure(text="Title, creator, platform and duration will appear after analysis.")
        self.media_badge.configure(text="WAITING FOR LINK", fg_color=SURFACE_2, text_color=MUTED)
        self.media_action_hint.configure(text="Analyze a link first, then add it to the download list.")
        self._apply_thumbnail(None)
        self._set_status("Ready for the next link", "ready")
        self.url_entry.focus_set()

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

    def check_for_updates(self, manual: bool = True, inline: bool = False, automatic: bool = False) -> None:
        """``inline``: the toolbar's Updates button - the answer is shown under the button,
        without a message box, and a found update is shown even if it was postponed."""
        if self.update_checking:
            return
        self.update_checking = True
        self._inline_update_check = inline
        if not automatic or not getattr(self, "latest_release", None):
            self._set_update_badge("checking")
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

    def _set_update_badge(self, state: str, text: str = "") -> None:
        badge = getattr(self, "top_update_button", None)
        if badge is not None and hasattr(badge, "set_state"):
            try:
                badge.set_state(state, text)
            except Exception:
                pass

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

        inline = bool(getattr(self, "_inline_update_check", False))
        self._inline_update_check = False
        if manual or inline or not self._is_update_snoozed(release.version):
            self.top_update_button.configure(text=f"v{release.version} available — please update")
            self.top_update_button.grid()
            if getattr(self, "_announced_update", "") != release.version and self.tray is not None:
                self._announced_update = release.version
                try:
                    self.tray.notify(f"Version {release.version} is available. Click Install update on the toolbar.",
                                     APP_NAME)
                except Exception:
                    pass
        else:
            self._set_update_badge("idle")

        if self.auto_download_updates_var.get() and not self.downloaded_update:
            self._start_update_download(manual=False)

    def _handle_update_current(self, payload: dict[str, Any]) -> None:
        self.update_checking = False
        self.update_check_button.configure(text="Check for updates", state="normal")
        self.update_status_label.configure(text=f"Current version: v{APP_VERSION}")
        self.update_detail_label.configure(text="You’re up to date.")
        self.update_notes_label.configure(text="")
        self.update_progress.set(0)
        self._set_update_badge("current")
        self._inline_update_check = False
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

        if self.latest_release:
            self._set_update_badge("available", f"v{self.latest_release.version} available")
        else:
            self._set_update_badge("error")
        self._inline_update_check = False
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
        self.top_update_button.configure(text="Restart to update")
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
        if self._update_handoff_started:
            return
        if not self.latest_release or not self.downloaded_update:
            return
        if not is_frozen_windows_app():
            # Started from Python source (python app.py / launcher.py): there is no EXE to
            # replace. Do not leave the user at a dead end: offer the installer instead.
            where = Path(__file__).resolve().parent
            url = self.latest_release.installer_url or self.latest_release.asset_url
            append_update_log(f"Source run at {where}: offering the installer for {self.latest_release.version}.")
            if messagebox.askyesno(
                APP_NAME,
                f"This copy of Media Downloader runs from Python source:\n{where}\n\n"
                "It cannot replace itself. Download and run the installer for "
                f"v{self.latest_release.version} instead?",
            ):
                webbrowser.open(url or LATEST_RELEASE_WEB)
            return

        active_downloads = self.download_queue.queued_count() + self.download_queue.running_count()
        paused_downloads = sum(
            item.get("status") == "paused" for item in self.download_queue.snapshot()
        )
        if active_downloads or paused_downloads or self.analysis_running:
            if not messagebox.askyesno(
                APP_NAME,
                "Installing the update requires exiting Media Downloader completely, "
                "including the system tray. Active downloads will be stopped and "
                "queued/paused jobs will not be kept after restart.\n\n"
                f"Active/queued: {active_downloads}  -  Paused: {paused_downloads}\n\n"
                "Finish important downloads first, or choose Yes to update now.",
                parent=self,
            ):
                return

        bundled = get_bundled_updater_path()
        if not bundled:
            messagebox.showerror(APP_NAME, "The updater component is missing from this build.")
            append_update_log("Updater component missing from bundled executable.")
            return

        try:
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            # Avoid clobbering a previous still-running updater process.
            updater_copy = UPDATE_DIR / f"MediaDownloaderUpdater-run-{uuid.uuid4().hex}.exe"
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
            self._update_handoff_started = True
            self.update_action_button.configure(state="disabled")
            mode = "installer" if is_installed_mode() else "portable"
            append_update_log(f"Updater launched for version {self.latest_release.version} in {mode} mode.")
            self.update_detail_label.configure(text="Restarting to complete the update…")
            # Standard quit also stops the tray event loop, unlike destroy().
            self.after(250, lambda: self._close_app(for_update=True))
        except Exception as exc:
            self._update_handoff_started = False
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

    # ---- start with Windows --------------------------------------------------------
    def _sync_start_with_windows(self) -> None:
        """The Run key is the source of truth; refresh its command so it follows app updates/moves."""
        enabled = win_startup.is_enabled()
        self.settings["start_with_windows"] = enabled
        if enabled:
            try:
                win_startup.set_enabled(True)
            except OSError as exc:
                LOGGER.warning("Could not refresh the start-with-Windows entry: %s", exc)

    # ---- integrations: engine options, hotkeys, browser extension, palette ----------
    def _configure_engine(self) -> None:
        s = self.settings
        engine_options.configure(
            cookies_file=str(s.get("cookies_file") or ""),
            cookies_browser="" if s.get("cookies_browser") == "None" else str(s.get("cookies_browser") or ""),
            skip_existing=bool(s.get("skip_existing", True)),
            fast_mode=bool(s.get("fast_mode", True)),
            embed_metadata=bool(s.get("embed_metadata", True)),
            embed_thumbnail=bool(s.get("embed_thumbnail", False)),
            subtitles=str(s.get("subtitles") or "Off"),
            subtitle_langs=str(s.get("subtitle_langs") or "en"),
        )

    def _start_integrations(self) -> None:
        self._sync_hotkeys()
        self._refresh_browser_extension()
        self._ensure_extension_server()

    def _ensure_extension_server(self) -> None:
        """Keep the browser link listening, like IDM/NDM: it is never switched off. The
        "take over browser downloads" option only decides whether the extension catches the
        browser's own downloads. If no port is free yet, try again a little later."""
        if self.extension_server is not None:
            return
        server = ExtensionServer(
            token=str(self.settings.get("extension_token") or ""),
            on_download=lambda data: bool(self.events.put(("external_download", dict(data))) or True),
            app_version=APP_VERSION,
            on_analyze=self._analyze_for_extension,
            capture=bool(self.settings.get("extension_enabled", True)),
        )
        if server.start():
            self.extension_server = server
            # Pages whose player only works in a real browser (embedded anime/movie players)
            # are opened in the user's own browser; the extension reports the stream back.
            engine_options.configure(
                browser_bridge=lambda url, cancel_event, srv=server: srv.resolve_in_browser(
                    url, cancel_event=cancel_event
                )
            )
            LOGGER.info("Browser extension server listening on 127.0.0.1:%s", server.port)
        else:
            LOGGER.warning("Browser extension server could not bind a port; retrying in 5 s")
            self.after(5000, self._ensure_extension_server)

    def _sync_hotkeys(self) -> None:
        wanted = bool(self.settings.get("clipboard_hotkey", True))
        if wanted and not self._hotkeys:
            for vk, mode, ident in ((0x44, "Video", 0x4D44), (0x4D, "Audio", 0x4D4D)):
                hotkey = GlobalHotkey(
                    lambda m=mode: self.events.put(("hotkey_clipboard", {"mode": m})),
                    modifiers=MOD_CONTROL | MOD_ALT, vk=vk, hotkey_id=ident,
                )
                if hotkey.start():
                    self._hotkeys.append(hotkey)
                else:
                    LOGGER.warning("Global hotkey %s unavailable (already in use?)", mode)
        elif not wanted and self._hotkeys:
            for hotkey in self._hotkeys:
                hotkey.stop()
            self._hotkeys = []

    def _stop_integrations(self) -> None:
        for hotkey in self._hotkeys:
            hotkey.stop()
        self._hotkeys = []
        if self.extension_server is not None:
            self.extension_server.stop()
            self.extension_server = None
            engine_options.configure(browser_bridge=None)

    def _default_batch_options(self, mode: str | None = None) -> dict[str, str]:
        return {
            "mode": mode or str(self.settings.get("default_mode") or "Video"),
            "video_quality": str(self.settings.get("video_quality") or "720p"),
            "audio_format": str(self.settings.get("audio_format") or "MP3"),
            "audio_quality": str(self.settings.get("audio_quality") or "192"),
        }

    def _download_clipboard_link(self, mode: str | None = None) -> None:
        try:
            text = self.clipboard_get()
        except Exception:
            text = ""
        try:
            urls, _dupes, _bad = parse_batch_urls_detailed(text)
        except ValueError:
            if self.tray is not None:
                self.tray.notify("No link found in the clipboard.", "Media Downloader")
            self._set_status("No link found in the clipboard.", "error")
            return
        try:
            self._enqueue_batch_downloads("\n".join(urls), self._default_batch_options(mode))
        except (ValueError, OSError) as exc:
            self._set_status(str(exc), "error")
            return
        message = f"Added {len(urls)} link{'s' if len(urls) != 1 else ''} from the clipboard"
        if self.tray is not None:
            self.tray.notify(message, "Media Downloader")

    def _analyze_for_extension(self, data: dict[str, Any]) -> dict[str, Any]:
        """Answer the extension's "which qualities does this video have?" (runs on the local
        server's thread). Uses the browser's own cookies, so logged-in Facebook/Instagram
        posts work, and never opens a browser tab."""
        url = str(data.get("url") or "").strip()
        page_url = str(data.get("pageUrl") or "").strip()
        cookie_path: Path | None = None
        cookies = data.get("cookies")
        if isinstance(cookies, list) and cookies:
            try:
                cookie_path = write_cookie_file(EXT_COOKIE_DIR, uuid.uuid4().hex, cookies[:2000])
            except OSError:
                cookie_path = None
        engine_options.set_request_context(
            cookiefile=str(cookie_path or ""),
            referer=page_url if page_url.startswith(("http://", "https://")) else "",
            allow_browser_bridge=False,
            quick=True,
        )
        try:
            summary, info = shared_analyze_url(url, cancel_event=threading.Event())
            _remember_ext_analysis(url, summary, info)
        except Exception as exc:
            LOGGER.info("Extension analysis failed url=%s: %s", url, exc)
            message = " ".join(str(exc).split())
            return {"ok": False, "error": message[:240] or "This video could not be read."}
        finally:
            engine_options.clear_request_context()
            if cookie_path is not None:
                try:
                    cookie_path.unlink(missing_ok=True)
                except OSError:
                    pass
        heights = sorted(
            {int(q[:-1]) for q in summary.get("qualities") or [] if str(q).endswith("p") and str(q)[:-1].isdigit()},
            reverse=True,
        )
        return {
            "ok": True,
            "title": str(summary.get("title") or ""),
            "duration": float(summary.get("duration") or 0),
            "platform": str(summary.get("platform") or ""),
            "qualities": [f"{h}p" for h in heights],
        }

    def _handle_external_download(self, data: dict[str, Any]) -> None:
        url = str(data.get("url") or "").strip()
        extra: dict[str, Any] = {}
        referer = str(data.get("referer") or "").strip()
        if referer.startswith(("http://", "https://")):
            extra["referer"] = referer
        headers = data.get("headers")
        if isinstance(headers, dict) and headers:
            extra["http_headers"] = {str(k): str(v) for k, v in list(headers.items())[:10]}
        title = " ".join(str(data.get("title") or "").split())[:180]
        if title:
            extra["title_hint"] = title
        page_url = str(data.get("pageUrl") or "").strip()
        kind = str(data.get("kind") or "")
        if title and kind != "file":
            # A site may keep the title of another episode: the address knows which one is playing.
            title = reconcile_title(title, page_url)[:180]
            extra["title_hint"] = title
        # A download the browser started ("file"), or a right-clicked link to a zip/exe/pdf...,
        # is saved as-is instead of being read as a video page.
        direct = kind == "file" or (kind != "media" and file_download.looks_like_file(url))
        if direct:
            name = file_download.clean_filename(str(data.get("fileName") or ""), "") or file_download.name_from_url(url)
            extra["direct"] = True
            extra["file_name"] = name or "download"
        if page_url.startswith(("http://", "https://")):
            extra["page_url"] = page_url
        if page_url.startswith(("http://", "https://")) and not direct:
            # A short-lived CDN playlist of a site yt-dlp knows (e.g. Eporner): download the
            # page instead, which gets fresh links; the captured stream stays as a fallback.
            try:
                better = preferred_page_url(url, page_url)
            except Exception:
                better = None
            if better:
                extra["fallback_url"] = url
                url = better
            else:
                # A stream on the site's own CDN (PornHub: phncdn.com) is tried first; if the
                # CDN refuses it, the page is read instead, as "Paste URL" would.
                try:
                    page_fallback = page_fallback_url(url, page_url)
                except Exception:
                    page_fallback = None
                if page_fallback:
                    extra["fallback_url"] = page_fallback
        options = self._default_batch_options()
        quality = str(data.get("quality") or "")
        if is_valid_quality(quality):
            options["video_quality"] = quality
        if str(data.get("mode") or "") in {"Video", "Audio"}:
            options["mode"] = str(data["mode"])
        cookies = data.get("cookies")
        if isinstance(cookies, list) and cookies:
            try:
                path = write_cookie_file(EXT_COOKIE_DIR, uuid.uuid4().hex, cookies[:2000])
            except OSError:
                path = None
            if path is not None:
                extra["cookiefile"] = str(path)
        if bool(self.settings.get("capture_dialog", True)):
            self._open_capture_dialog(url, options, extra, direct=direct, data=data)
            return
        try:
            self._enqueue_batch_downloads(url, options, extra=extra)
        except (ValueError, OSError) as exc:
            LOGGER.warning("Extension link rejected: %s", exc)

    # ------------------------------------------------------------------ download window
    def _open_capture_dialog(
        self, url: str, options: dict[str, str], extra: dict[str, Any], *, direct: bool, data: dict[str, Any]
    ) -> None:
        from capture_dialog import CaptureApi, CaptureDialog

        page_url = str(extra.get("page_url") or "")
        site_url = page_url or url
        if direct:
            name = str(extra.get("file_name") or "download")
        else:
            name = str(extra.get("title_hint") or "")
            if not name or is_placeholder_title(name):
                name = urlsplit(site_url).path.rstrip("/").split("/")[-1] or urlsplit(site_url).netloc or "video"
            name = " ".join(name.split())[:150]
        try:
            folder, _quality = resolve_target(
                site_url,
                rules=parse_rules(str(self.settings.get("folder_rules") or "")),
                default_dir=self.download_dir,
                default_quality=str(options.get("video_quality") or "720p"),
                organize_by_site=bool(self.settings.get("organize_by_site", False)),
                platform=platform_name(site_url),
            )
        except Exception:
            folder = self.download_dir
        host = urlsplit(site_url).netloc.lower()
        ext = os.path.splitext(name)[1].lower() if direct else ""
        remembered = dict(self.settings.get("capture_dirs") or {})
        for key in ((f"ext:{ext}" if ext else ""), f"site:{host}"):
            saved = str(remembered.get(key) or "") if key else ""
            if saved and os.path.isdir(saved):
                folder = Path(saved)
                break
        else:
            if os.path.normcase(str(folder)) == os.path.normcase(str(self.download_dir)):
                last = str(self.settings.get("capture_last_dir") or "")
                if last and os.path.isdir(last):
                    folder = Path(last)
        try:
            size = int(data.get("fileSize") or 0) if direct else 0
        except (TypeError, ValueError):
            size = 0

        def start(final_name: str, folder_text: str, hold: bool, remember: dict[str, bool]) -> str | None:
            ex = dict(extra)
            if direct:
                cleaned = file_download.clean_filename(final_name, "")
                ex["file_name"] = cleaned or ex.get("file_name") or "download"
            else:
                ex["custom_title"] = final_name
            ids: list[str] = []
            self._enqueue_batch_downloads(
                url, options, extra=ex, target_dir_override=Path(folder_text), hold=hold, ids_out=ids)
            if not ids:
                return None
            self._capture_ids.add(ids[0])
            self.settings["capture_last_dir"] = folder_text
            saved = dict(self.settings.get("capture_dirs") or {})
            for key, wanted in ((f"ext:{ext}" if ext else "", remember.get("ext")),
                                (f"site:{host}" if host else "", remember.get("site"))):
                if not key:
                    continue
                if wanted:
                    saved[key] = folder_text
                else:
                    saved.pop(key, None)   # the box was cleared: stop using a saved folder
            self.settings["capture_dirs"] = saved
            save_settings(self.settings)
            return ids[0]

        def state(request_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
            with self._download_progress_lock:
                metrics = dict(self._latest_download_metrics.get(request_id) or {})
            return self.download_queue.get(request_id), metrics

        def deleted(_request_id: str, _path: str) -> None:
            try:
                self.history_store.prune_missing()
                self._invalidate_history()
            except Exception:
                pass

        api = CaptureApi(
            start=start, state=state, pause=self.pause_download, resume=self.resume_download,
            cancel=self.cancel_download, retry=self.retry_download, deleted=deleted,
        )
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            self._capture_dialogs = [d for d in self._capture_dialogs
                                     if isinstance(d, native_forms.CaptureForm) and not d.done]
            form = native_forms.CaptureForm(
                shell, api, name=name, folder=str(folder), source=host, size_bytes=size,
                is_file=direct, offset=len(self._capture_dialogs), ext=ext, host=host,
                ext_saved=_same_folder(remembered.get(f"ext:{ext}") if ext else "", folder),
                site_saved=_same_folder(remembered.get(f"site:{host}"), folder),
            )
            self._capture_dialogs.append(form)
            shell.show_form(form)
            return
        self._capture_dialogs = [d for d in self._capture_dialogs if _widget_alive(d)]
        dialog = CaptureDialog(
            self, api, name=name, folder=str(folder), source=host, size_bytes=size,
            is_file=direct, offset=len(self._capture_dialogs), ext=ext, host=host,
            ext_saved=_same_folder(remembered.get(f"ext:{ext}") if ext else "", folder),
            site_saved=_same_folder(remembered.get(f"site:{host}"), folder),
        )
        self._capture_dialogs.append(dialog)

    def export_browser_extension(self) -> None:
        from browser_extension import export_extension

        target = EXTENSION_DIR
        try:
            export_extension(target, str(self.settings.get("extension_token") or ""))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not export the extension.\n\n{exc}")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self.clipboard_clear()
            self.clipboard_append("chrome://extensions")
        except Exception:
            pass
        messagebox.showinfo(
            APP_NAME,
            f"The browser extension is ready in:\n{target}\n\n"
            "1. Open your browser's extensions page (chrome://extensions is on the clipboard; Edge: edge://extensions).\n"
            "2. Turn on Developer mode and choose Load unpacked, then pick that folder.\n"
            "   Already installed? Just press Reload on the extension.\n\n"
            "It connects to Media Downloader by itself - there is no token to copy. From then on, files you "
            "download in the browser are downloaded by Media Downloader.",
            parent=self,
        )

    def _refresh_browser_extension(self) -> None:
        """After an update, rewrite the extension folder the user installed from (if any)."""
        from browser_extension import refresh_exported_extension

        try:
            if refresh_exported_extension(EXTENSION_DIR, str(self.settings.get("extension_token") or "")):
                LOGGER.info("Browser extension files updated in %s", EXTENSION_DIR)
        except OSError as exc:
            LOGGER.warning("Could not update the browser extension files: %s", exc)

    def open_command_palette(self) -> None:
        existing = getattr(self, "_palette", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.destroy()
        except Exception:
            pass
        commands: list[tuple[str, Any]] = [
            ("Add URL…", lambda: self.show_page("download")),
            ("Add batch download…", lambda: self.show_page("batch")),
            ("Show all downloads", lambda: self._show_category("all")),
            ("Show unfinished downloads", lambda: self._show_category("unfinished")),
            ("Show finished downloads", lambda: self._show_category("finished")),
            ("Updates & about", lambda: self.show_page("updates")),
            ("Download link from clipboard (video)", lambda: self._download_clipboard_link("Video")),
            ("Download link from clipboard (audio only)", lambda: self._download_clipboard_link("Audio")),
            ("Pause all downloads", self.pause_all_downloads),
            ("Resume all downloads", self.resume_all_downloads),
            ("Retry all failed downloads", self.retry_all_failed),
            ("Clear finished downloads", self.clear_finished_downloads),
            ("Cancel all downloads", self.cancel_all_downloads),
            ("Open download folder", self.open_download_folder),
            ("Open media editor", self.open_editor),
            ("Open screen recorder", self.open_screen_recorder),
            ("Open audio recorder", self.open_audio_recorder),
            ("Delete completed downloads…", self.delete_completed),
            ("Open settings", self.open_settings),
            ("Export browser extension", self.export_browser_extension),
            ("Check for updates", lambda: self.check_for_updates(manual=True)),
        ]
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            for form in list(shell.forms.values()):
                if isinstance(form, native_forms.PaletteForm):
                    form.close()
            shell.show_form(native_forms.PaletteForm(shell, commands))
            return
        self._palette = CommandPalette(self, commands)

    def retry_all_failed(self) -> None:
        for item in self.download_queue.snapshot():
            if item.get("status") == "failed":
                self.download_queue.retry(str(item["id"]))
        self._schedule_download_activity_refresh(20)
        self._start_available_downloads()

    def show_failure_details(self, request_id: str) -> None:
        item = self.download_queue.get(request_id)
        if item is None:
            return
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            shell.show_form(native_forms.FailureForm(shell, item, self._retry_with_url))
            return
        FailureDialog(self, item, on_retry=self._retry_with_url)

    def _retry_with_url(self, request_id: str, url: str) -> None:
        if not self.download_queue.update_url(request_id, url):
            self._set_status("This download can no longer be edited.", "error")
            return
        state = self.download_queue.get(request_id) or {}
        if state.get("status") == "queued":
            self.download_queue.release_auto_retry(request_id)
            self._schedule_download_activity_refresh(20)
            self._start_available_downloads()
        else:
            self.retry_download(request_id)

    def _release_auto_retry(self, request_id: str) -> None:
        if self.download_queue.release_auto_retry(request_id):
            self._schedule_download_activity_refresh(20)
            self._start_available_downloads()

    def _start_available_downloads(self) -> None:
        for request in self.download_queue.start_available():
            payload = dict(request.payload or {})
            url = str(payload.get("url") or "")
            name = str(payload.get("name") or "media_download")
            download_dir = Path(str(payload.get("download_dir") or self.download_dir))
            request_settings = dict(payload.get("request_settings") or {})
            cached_info = payload.get("cached_info")
            cached_info = cached_info if isinstance(cached_info, dict) else None
            cancel_event = threading.Event()
            self.download_cancel_events[request.id] = cancel_event

            LOGGER.info("Starting download request=%s url=%s", request.id, url)
            threading.Thread(
                target=self._download_worker,
                args=(request.id, cancel_event, url, name, download_dir, request_settings, cached_info),
                daemon=True,
                name=f"link-download-{request.id[:8]}",
            ).start()

        self._schedule_download_activity_refresh(20)

    def retry_download(self, request_id: str) -> None:
        if not self.download_queue.retry(request_id):
            self._set_status("This failed download could not be retried.", "error")
            self._schedule_download_activity_refresh(20)
            return

        LOGGER.info("Retry queued request=%s", request_id)
        self._set_status("Retry added to download queue", "ready")
        self._schedule_download_activity_refresh(20)
        self._start_available_downloads()

    def _reserve_name(self, request_id: str, base: str) -> str:
        """Keep concurrent downloads of identically-titled media from sharing a filename."""
        with self._name_lock:
            existing = self._reserved_names.get(request_id)
            if existing:
                return existing
            taken = set(self._reserved_names.values())
            candidate, index = base, 2
            while candidate in taken:
                candidate = f"{base} ({index})"
                index += 1
            self._reserved_names[request_id] = candidate
            return candidate

    def pause_download(self, request_id: str) -> None:
        state = self.download_queue.pause(request_id)
        if state == "pausing":
            self._pause_ids.add(request_id)
            event = self.download_cancel_events.get(request_id)
            if event is not None:
                event.set()
            self._set_status("Pausing download…", "working")
            self.after(3000, lambda: self._force_stop(request_id))
        elif state == "paused":
            self._set_status("Download paused", "ready")
        self._schedule_download_activity_refresh(20)

    def _force_stop(self, request_id: str) -> None:
        """Pause/cancel was asked but the worker is stuck (a slow fragment, ffmpeg...): settle it now."""
        snap = self.download_queue.get(request_id)
        if snap is None or snap.get("status") != "cancelling":
            return
        self._stopping_workers.add(request_id)
        if request_id in self._pause_ids:
            self._pause_ids.discard(request_id)
            self.download_queue.mark_paused(request_id)
            self._set_status("Download paused", "ready")
        else:
            self._forced_cancelled.add(request_id)
            self.download_queue.cancel(request_id)
            self._record_history(request_id, "cancelled")
            self._set_status("Download cancelled", "cancelled")
        self._schedule_download_activity_refresh(20)

    def resume_download(self, request_id: str) -> None:
        if request_id in self._stopping_workers:
            # The old worker is still winding down; starting a second one would fight over the files.
            self.after(1000, lambda: self.resume_download(request_id))
            return
        if self.download_queue.resume(request_id):
            self._set_status("Download resumed", "working")
            self._start_available_downloads()
        self._schedule_download_activity_refresh(20)

    def pause_all_downloads(self) -> None:
        for item in self.download_queue.snapshot():
            if str(item.get("status") or "") in {"queued", "running"}:
                self.pause_download(str(item.get("id") or ""))

    def resume_all_downloads(self) -> None:
        for item in reversed(self.download_queue.snapshot()):
            if str(item.get("status") or "") == "paused":
                self.resume_download(str(item.get("id") or ""))

    def cancel_download(self, request_id: str) -> None:
        self._pause_ids.discard(request_id)
        state = self.download_queue.request_cancel(request_id)
        if state == "cancelling":
            event = self.download_cancel_events.get(request_id)
            if event is not None:
                event.set()
            self._set_status("Cancelling download…", "working")
            self.after(3000, lambda: self._force_stop(request_id))
        elif state == "cancelled":
            self._set_status("Queued download cancelled", "cancelled")
            self._start_available_downloads()
        self._schedule_download_activity_refresh(20)

    def clear_finished_downloads(self) -> None:
        removed = self.download_queue.clear_finished()
        if removed:
            self._set_status(f"Cleared {removed} finished item{'s' if removed != 1 else ''}", "ready")
        self._schedule_download_activity_refresh(20)

    def cancel_all_downloads(self) -> None:
        for item in self.download_queue.snapshot():
            if str(item.get("status") or "") in {"queued", "running", "cancelling", "paused"}:
                self.cancel_download(str(item.get("id") or ""))

    def _sync_recent_file(self) -> None:
        recent = self.history_store.most_recent_existing()
        self.last_file = Path(str(recent["file_path"])) if recent else None
        if hasattr(self, "open_file_button"):
            self.open_file_button.configure(state="normal" if self.last_file else "disabled")

    # ------------------------------------------------------------ history page
    def open_history(self) -> None:
        self.show_page("history")

    def _record_history(
        self,
        request_id: str,
        status: str,
        *,
        path: Path | None = None,
        detail: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """Save one finished/failed/cancelled download with everything Properties shows."""
        if request_id in getattr(self, "_discard_ids", ()):
            return  # deleted from the list while it was still running
        try:
            snapshot = self.download_queue.get(request_id) or {}
            queued = self.download_queue.payload(request_id) or {}
            settings = dict(queued.get("request_settings") or {})
            detail = dict(detail or {})
            probe = dict(detail.get("probe") or {})
            source_url = str(detail.get("source_url") or settings.get("source_url") or queued.get("url") or "")
            started = float(snapshot.get("started_at") or snapshot.get("created_at") or 0)
            finished = float(snapshot.get("finished_at") or 0) or time.time()
            mode = str(settings.get("mode") or "Video")
            title = (detail.get("title") or settings.get("history_title") or settings.get("title_hint")
                     or (path.stem if path else snapshot.get("name")) or "Media")
            entry = make_history_entry(
                path,
                status=status,
                title=str(title),
                source_url=source_url,
                platform=str(detail.get("platform") or settings.get("history_platform") or ""),
                creator=str(detail.get("creator") or settings.get("history_creator") or ""),
                mode=mode,
                quality=str(detail.get("quality") or settings.get("history_quality") or ""),
                duration_seconds=int(probe.get("duration_seconds") or detail.get("duration_seconds")
                                     or settings.get("history_duration") or 0),
                started_at=started,
                finished_at=finished,
                page_url=str(settings.get("page_url") or source_url),
                download_url=str(queued.get("url") or source_url),
                referer=str(settings.get("referer") or ""),
                http_headers={k: v for k, v in dict(settings.get("http_headers") or {}).items()
                              if str(k).lower() in {"origin", "user-agent"}},
                download_dir=str(path.parent if path else queued.get("download_dir") or ""),
                audio_format=str(settings.get("audio_format") or ""),
                audio_quality=str(settings.get("audio_quality") or ""),
                video_quality=str(settings.get("video_quality") or ""),
                batch_id=str(settings.get("batch_id") or ""),
                attempts=int(detail.get("attempts") or 1),
                error=(str(error or snapshot.get("last_error") or "")[:2000] if status != "completed" else ""),
                resolution=str(probe.get("resolution") or ""),
                video_codec=str(probe.get("video_codec") or ""),
                audio_codec=str(probe.get("audio_codec") or ""),
                container=str(probe.get("container") or ""),
                bitrate_kbps=int(probe.get("bitrate_kbps") or 0),
                request_id=request_id,
            )
            self.history_store.add(entry)
            LOGGER.info("History recorded status=%s request=%s", status, request_id)
        except Exception:
            LOGGER.exception("Could not save download history request=%s", request_id)
            return
        invalidate = getattr(self, "_invalidate_history", None)
        if invalidate is not None:
            invalidate()

    def redownload_from_history(self, entry: dict[str, Any], *, resume: bool = False) -> None:
        """IDM-style Resume (continue a failed/cancelled download) or Redownload (fresh copy)."""
        url = str(entry.get("download_url") or entry.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showerror(APP_NAME, "This history item has no download link.")
            return
        mode = str(entry.get("mode") or "Video")
        options = self._default_batch_options(mode)
        quality = str(entry.get("video_quality") or "")
        if is_valid_quality(quality):
            options["video_quality"] = quality
        audio_format = str(entry.get("audio_format") or "").upper()
        if audio_format in AUDIO_FORMATS:
            options["audio_format"] = audio_format
        if str(entry.get("audio_quality") or "") in {"128", "192", "256", "320"}:
            options["audio_quality"] = str(entry["audio_quality"])
        extra: dict[str, Any] = {"title_hint": str(entry.get("title") or "")}
        page_url = str(entry.get("page_url") or "")
        if page_url.startswith(("http://", "https://")):
            extra["page_url"] = page_url
        referer = str(entry.get("referer") or "")
        if referer.startswith(("http://", "https://")):
            extra["referer"] = referer
        headers = entry.get("http_headers")
        if isinstance(headers, dict) and headers:
            extra["http_headers"] = {str(k): str(v) for k, v in headers.items()}
        if not resume:
            extra["force_redownload"] = True
        try:
            self._enqueue_batch_downloads(url, options, extra=extra)
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_NAME, "Could not add the download." + chr(10) + chr(10) + str(exc))
            return
        self._set_status("Resuming download" if resume else "Downloading again", "working")
        self.show_page("queue")

    # ----------------------------------------------------------- settings page
    def open_settings(self, tab: str | None = None) -> None:
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            shell.open_settings(tab)
        else:
            self._open_settings_dialog(tab)

    def _persist_settings(self) -> None:
        save_settings(self.settings)

    def _build_options_dialog(self, on_close: Any) -> OptionsDialog:
        server = self.extension_server
        if server is None:
            status = "Starting the browser link…"
        elif server.extension_active():
            status = "Connected: the browser extension is talking to Media Downloader."
        else:
            status = "Ready: install the extension and it connects by itself."
        return OptionsDialog(
            self,
            self.settings,
            on_save=self._apply_settings,
            on_check_updates=lambda: (self.show_page("updates"), self.check_for_updates(manual=True)),
            on_open_diagnostics=self.open_diagnostics,
            on_open_log=self.open_app_log,
            on_open_release=self.open_release_page,
            on_export_extension=self.export_browser_extension,
            on_close=on_close,
            extension_status=status,
            icon=str(_icon_path()) if _icon_path() else None,
        )

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
        theme_changed = normalized["theme"] != theme.normalize_theme(self.settings.get("theme"))
        wants_startup = bool(normalized.get("start_with_windows"))
        if win_startup.supported() and wants_startup != win_startup.is_enabled():
            try:
                win_startup.set_enabled(wants_startup)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"Could not change the start-with-Windows setting.\n\n{exc}") from exc
            LOGGER.info("Start with Windows %s", "enabled" if wants_startup else "disabled")
        if not save_settings(normalized):
            raise RuntimeError(f"Could not save settings to {CONFIG_FILE}.")
        self.settings = normalized
        self.download_queue.max_concurrent = normalized["max_concurrent_downloads"]
        self._configure_engine()
        self._sync_hotkeys()
        if self.extension_server is not None:
            self.extension_server.capture = bool(normalized.get("extension_enabled", True))
        else:
            self._ensure_extension_server()

        self.download_dir = download_dir
        self.download_dir_var.set(str(download_dir))
        self.mode_var.set(str(normalized["default_mode"]))
        self.video_quality_var.set(str(normalized["video_quality"]))
        self.audio_format_var.set(str(normalized["audio_format"]))
        self.audio_quality_var.set(str(normalized["audio_quality"]))
        self.open_editor_after_var.set(bool(normalized["open_editor_after_download"]))
        self.auto_check_updates_var.set(bool(normalized["auto_check_updates"]))
        self.auto_download_updates_var.set(bool(normalized["auto_download_updates"]))

        self.save_location_label.configure(text=f"Saving to  {self.download_dir}")
        self._sync_mode(str(normalized["default_mode"]))
        self.batch_page.set_defaults(normalized)
        # Fill newly available slots immediately when the user raises the limit.
        self._start_available_downloads()
        if hasattr(self, "_theme_var"):
            self._theme_var.set(normalized["theme"])
        if theme_changed:
            self.after(200, lambda: messagebox.showinfo(
                APP_NAME, f"The {normalized['theme'].lower()} theme will be used the next time {APP_NAME} starts.",
                parent=self.settings_window or self))

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
        shell = getattr(self, "native_shell", None)
        if shell is not None:
            import native_forms

            if shell.diagnostics is None or shell.diagnostics.done:
                shell.diagnostics = native_forms.DiagnosticsForm(shell)
                shell.show_form(shell.diagnostics)
            else:
                shell.diagnostics.flash = not shell.diagnostics.flash
            return
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
