from __future__ import annotations

import copy
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
import uuid
from urllib.parse import urlsplit
import webbrowser
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, messagebox

from app_logging import get_logger, log_path
from diagnostics_window import DiagnosticsWindow
from batch_download import batch_filename, parse_batch_urls
from app_ui import UIMixin
import ui_kit as ui
from download_queue import DownloadQueue, DownloadRequest
from history_store import HistoryStore, make_history_entry
from history_window import HistoryWindow
from install_mode import is_installed_mode
from media_editor import MediaEditorWindow
from media_sources import detect_platform, platform_name
from shared_engine import Cancelled as SharedCancelled, analyze_url as shared_analyze_url, download_from_analysis as shared_download_from_analysis
from settings_window import SettingsWindow
from update_manager import LATEST_RELEASE_WEB, ReleaseInfo, download_installer_release, download_release, fetch_latest_release, is_newer_version
from version import APP_VERSION

APP_NAME = "Media Downloader"
APP_USER_MODEL_ID = "TeamFahad.MediaDownloader"


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


class DownloaderApp(UIMixin, ctk.CTk):
    def __init__(self) -> None:
        if os.name == "nt":
            try:  # own taskbar identity so the download icon shows (and pins) instead of python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
            except Exception:
                pass
        super().__init__()
        self.title(APP_NAME)
        self._apply_window_icon()
        self.geometry("1180x780")
        self.minsize(980, 640)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._close_app)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_info: dict[str, Any] | None = None
        self.thumbnail_image: Any = None
        self.last_file: Path | None = None
        self.editor_window: MediaEditorWindow | None = None
        self.diagnostics_window: DiagnosticsWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.history_window: HistoryWindow | None = None
        self.download_queue = DownloadQueue(max_concurrent=3)
        self.task_state = TaskState.IDLE

        self._analysis_counter = 0
        self.analysis_job_id: int | None = None
        self.analysis_cancel_event: threading.Event | None = None
        self.analysis_url = ""
        self.analysis_running = False

        self.download_cancel_events: dict[str, threading.Event] = {}
        self._download_progress_lock = threading.Lock()
        self._latest_download_progress: dict[str, dict[str, Any]] = {}
        self._download_activity_after_id: str | None = None

        self.settings = load_settings()
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
        LOGGER.info("App started version=%s executable=%s", APP_VERSION, sys.executable)
        self.after(120, self._drain_events)
        self.after(700, lambda: self._show_update_result(attempt=0))
        if self.auto_check_updates_var.get():
            self.after(1500, lambda: self.check_for_updates(manual=False))

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
        # CustomTkinter swaps in its own icon shortly after start-up; set ours again afterwards.
        self.after(250, apply)

    def _close_app(self) -> None:
        active_downloads = self.download_queue.queued_count() + self.download_queue.running_count()
        active = self.analysis_running or self.update_downloading or active_downloads > 0
        if active:
            should_confirm = bool(self.settings.get("confirm_before_exit", True))
            if should_confirm and not messagebox.askyesno(
                APP_NAME,
                "Tasks are still running. Cancel them and close Media Downloader?",
                parent=self,
            ):
                return

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
        self.destroy()

    def _center_window(self) -> None:
        self.update_idletasks()
        width, height = 1180, 780
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
        self._set_status("Analysis timed out after 60 seconds", "error")
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
        self.after(60_000, lambda jid=job_id, source=url: self._analysis_watchdog(jid, source))

    def _analyze_media_worker(self, job_id: int, cancel_event: threading.Event, url: str) -> None:
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
        self, text: str, options: dict[str, str]
    ) -> tuple[str, int, int]:
        urls, duplicates = parse_batch_urls(text)
        mode = str(options.get("mode") or "Video")
        quality = str(options.get("video_quality") or "720p")
        audio_format = str(options.get("audio_format") or "MP3")
        bitrate = str(options.get("audio_quality") or "192")
        if mode not in {"Video", "Audio"}:
            raise ValueError("Select Video or Audio.")
        if quality not in {"Best available", "1080p", "720p", "480p", "360p"}:
            raise ValueError("Invalid video quality.")
        if audio_format not in {"MP3", "M4A"} or bitrate not in {"128", "192", "256", "320"}:
            raise ValueError("Invalid audio output settings.")

        self.download_dir.mkdir(parents=True, exist_ok=True)
        batch_id = uuid.uuid4().hex
        payloads = []
        for index, url in enumerate(urls, 1):
            prefix = batch_id[:8]
            request_settings = {
                "batch_id": batch_id,
                "batch_index": index,
                "mode": mode,
                "video_quality": quality,
                "audio_format": audio_format.lower(),
                "audio_quality": bitrate,
                "source_url": url,
                "history_title": f"Batch item {index}",
                "history_platform": platform_name(url),
                "history_quality": quality if mode == "Video" else f"{audio_format} {bitrate} kbps",
                "history_duration": 0,
            }
            payloads.append({
                "url": url,
                "name": f"Link {index} • {urlsplit(url).hostname or 'media'}",
                "batch_id": batch_id,
                "batch_index": index,
                "download_dir": str(self.download_dir),
                "request_settings": request_settings,
                "cached_info": None,
            })
        self.download_queue.enqueue_batch(payloads)
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
        }

        request = self.download_queue.enqueue(
            {
                "url": url,
                "name": name,
                "download_dir": str(self.download_dir),
                "request_settings": request_settings,
                "cached_info": copy.deepcopy(info),
            },
            edit_after_download=bool(edit_after_download),
        )
        LOGGER.info("Queued link download request=%s url=%s", request.id, url)
        self._set_status(f"Added to downloads: {name}", "ready")
        self.media_action_hint.configure(text="Added to downloads. Paste another link whenever you want.")
        self._schedule_download_activity_refresh(20)
        self._start_available_downloads()

    def _put_download_event(self, kind: str, request_id: str, data: Any = None) -> None:
        if kind == "download_progress":
            with self._download_progress_lock:
                self._latest_download_progress[request_id] = dict(data or {})
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
        try:
            if request_settings.get("batch_id"):
                if cancel_event.is_set():
                    raise SharedCancelled("Batch item cancelled before analysis.")
                self._put_download_event(
                    "download_status", request_id, "Analyzing batch link…"
                )
                summary, extracted_info = shared_analyze_url(
                    url,
                    cancel_event=cancel_event,
                    on_status=lambda text: self._put_download_event(
                        "download_status", request_id, f"Analyze: {text}"
                    ),
                )
                if cancel_event.is_set():
                    raise SharedCancelled("Batch item cancelled after analysis.")
                cached_info = extracted_info
                name = batch_filename(
                    str(request_settings["batch_id"]),
                    int(request_settings.get("batch_index") or 0),
                    str(summary.get("title") or "media"),
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
            )

            self._put_download_event(
                "download_done",
                request_id,
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
        except SharedCancelled:
            self._put_download_event("download_cancelled", request_id, "Download cancelled.")
        except Exception as exc:
            if cancel_event.is_set():
                self._put_download_event("download_cancelled", request_id, "Download cancelled.")
            else:
                LOGGER.exception("Download request %s failed", request_id)
                self._put_download_event("download_error", request_id, str(exc))
        finally:
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

                if kind.startswith("download_"):
                    if not isinstance(payload, dict):
                        continue
                    request_id = str(payload.get("request_id") or "")
                    data = payload.get("data")
                    request_state = self.download_queue.get(request_id)

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
                        self.last_file = completed_path
                        self.task_state = TaskState.DOWNLOADED
                        self._set_status(f"Download completed: {completed_path.name}", "success")

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
                        self.download_queue.fail(request_id, str(data or "Download failed"))
                        self.task_state = TaskState.ERROR
                        self._set_status("A download failed. See Download Activity for details.", "error")
                        self._schedule_download_activity_refresh(30)

                    elif kind == "download_cancelled":
                        self.download_queue.cancel(request_id)
                        self._set_status("Download cancelled", "cancelled")
                        self._schedule_download_activity_refresh(30)

                    elif kind == "download_finished":
                        self.download_cancel_events.pop(request_id, None)
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

    def cancel_download(self, request_id: str) -> None:
        state = self.download_queue.request_cancel(request_id)
        if state == "cancelling":
            event = self.download_cancel_events.get(request_id)
            if event is not None:
                event.set()
            self._set_status("Cancelling download…", "working")
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
            if str(item.get("status") or "") in {"queued", "running", "cancelling"}:
                self.cancel_download(str(item.get("id") or ""))

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

        self.save_location_label.configure(text=f"Saving to  {self.download_dir}")
        self._sync_mode(str(normalized["default_mode"]))
        self.batch_page.set_defaults(normalized)

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
