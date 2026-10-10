from __future__ import annotations

import os
import platform
import secrets
import shutil
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from imageio_ffmpeg import get_ffmpeg_exe

from media_core.browser_resolver import find_browser
from media_core.editor import extract_preview_frame, extract_waveform, probe_media, public_media_info, render_proxy_clip, safe_export_name
from media_core.network import safe_proxy_label

from .agent_control import can_self_update, send_control
from .core_updater import CoreUpdateService
from .diagnostics import create_diagnostics_bundle
from .editor_library import EditorLibrary
from .jobs import JobManager
from .logging_setup import configure_logging
from .paths import (
    APP_DATA_DIR,
    DEFAULT_DOWNLOAD_DIR,
    DIAGNOSTICS_DIR,
    EDITOR_LIBRARY_FILE,
    LOG_FILE,
    SETTINGS_FILE,
    STATE_FILE,
    UPDATE_DIR,
    WEB_DIR,
)
from .settings_store import SettingsStore
from .version import CORE_VERSION
from .windows_startup import can_manage_startup, launch_at_login_enabled, set_launch_at_login


CORE_KEY = secrets.token_urlsafe(32)
LOGGER = configure_logging(LOG_FILE)
SETTINGS = SettingsStore(SETTINGS_FILE, default_download_dir=DEFAULT_DOWNLOAD_DIR)
CURRENT_SETTINGS = SETTINGS.get()
EDITOR_LIBRARY = EditorLibrary(EDITOR_LIBRARY_FILE)
MANAGER = JobManager(
    max_downloads=int(CURRENT_SETTINGS["max_concurrent_downloads"]),
    state_path=STATE_FILE,
    logger=LOGGER,
)
UPDATER = CoreUpdateService(
    current_version=CORE_VERSION,
    update_dir=UPDATE_DIR,
    channel=str(CURRENT_SETTINGS["update_channel"]),
    can_apply=can_self_update(),
)
LOGGER.info(
    "local_core_started version=%s max_downloads=%s update_channel=%s",
    CORE_VERSION,
    CURRENT_SETTINGS["max_concurrent_downloads"],
    CURRENT_SETTINGS["update_channel"],
)

app = FastAPI(
    title="Media Downloader Local Core",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=5000)


class DownloadRequest(BaseModel):
    analysis_id: str
    mode: str = "Video"
    video_quality: str = "Best available"
    audio_format: str = "MP3"
    audio_quality: str = "192"
    filename: str = "media_download"
    download_dir: str | None = None


class ConfigRequest(BaseModel):
    download_dir: str


class FolderPickerRequest(BaseModel):
    current_dir: str | None = None


class EditorPathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=5000)


class EditorPreviewRequest(EditorPathRequest):
    position: float = Field(default=0.0, ge=0)
    crop_preset: str = "Original"
    rotate: str = "0°"
    custom_x: int = Field(default=0, ge=0)
    custom_y: int = Field(default=0, ge=0)
    custom_width: int = Field(default=0, ge=0)
    custom_height: int = Field(default=0, ge=0)


class EditorProxyRequest(EditorPreviewRequest):
    duration: float = Field(default=6.0, ge=0.75, le=10.0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    mute: bool = False
    volume_percent: float = Field(default=100.0, ge=0, le=200)
    audio_preset: str = "Flat"
    noise_reduction: bool = False


class EditorExportRequest(EditorPathRequest):
    output_dir: str = Field(min_length=1, max_length=5000)
    output_name: str = Field(default="edited_media", min_length=1, max_length=260)
    start: float = Field(default=0.0, ge=0)
    end: float = Field(gt=0)
    crop_preset: str = "Original"
    rotate: str = "0°"
    custom_x: int = Field(default=0, ge=0)
    custom_y: int = Field(default=0, ge=0)
    custom_width: int = Field(default=0, ge=0)
    custom_height: int = Field(default=0, ge=0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    mute: bool = False
    volume_percent: float = Field(default=100.0, ge=0, le=200)
    fade_in: float = Field(default=0.0, ge=0, le=60)
    fade_out: float = Field(default=0.0, ge=0, le=60)
    audio_preset: str = "Flat"
    noise_reduction: bool = False
    quality: str = "Balanced"


class EditorLibrarySave(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    item_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SettingsPatch(BaseModel):
    download_dir: str | None = None
    default_mode: str | None = None
    video_quality: str | None = None
    audio_format: str | None = None
    audio_quality: str | None = None
    max_concurrent_downloads: int | None = Field(default=None, ge=1, le=6)
    auto_check_core_updates: bool | None = None
    update_channel: str | None = None
    update_policy: str | None = None
    update_install_hour: int | None = Field(default=None, ge=0, le=23)
    open_browser_on_start: bool | None = None
    tray_icon_enabled: bool | None = None
    launch_at_login: bool | None = None


def _custom_crop(payload: Any) -> tuple[int, int, int, int] | None:
    if str(getattr(payload, "crop_preset", "Original")) != "Custom":
        return None
    width = int(getattr(payload, "custom_width", 0) or 0)
    height = int(getattr(payload, "custom_height", 0) or 0)
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="Custom crop width and height must be greater than zero.")
    return (
        int(getattr(payload, "custom_x", 0) or 0),
        int(getattr(payload, "custom_y", 0) or 0),
        width,
        height,
    )


def _agent_snapshot() -> dict[str, Any]:
    base = {
        "connected": False,
        "self_update": can_self_update(),
        "rollback_available": False,
        "previous_path": "",
        "startup_management": can_manage_startup(),
        "launch_at_login": launch_at_login_enabled(),
    }
    try:
        ping = send_control("ping")
        base.update(
            connected=True,
            self_update=bool(ping.get("self_update", base["self_update"])),
            rollback_available=bool(ping.get("rollback_available")),
            previous_path=str(ping.get("previous_path") or ""),
            agent_version=str(ping.get("version") or ""),
            agent_pid=int(ping.get("pid") or 0),
        )
    except Exception:
        pass
    return base


def _agent_connected() -> bool:
    return bool(_agent_snapshot()["connected"])


class LocalRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    def check(self, action: str, *, limit: int, window_seconds: float) -> None:
        now = time.monotonic()
        cutoff = now - float(window_seconds)
        with self._lock:
            events = self._events[action]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= int(limit):
                retry_after = max(1, int(window_seconds - (now - events[0])))
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many {action} requests. Try again in about {retry_after}s.",
                    headers={"Retry-After": str(retry_after)},
                )
            events.append(now)


RATE_LIMITER = LocalRateLimiter()


def require_key(
    x_media_core_key: Annotated[str | None, Header()] = None,
) -> None:
    if not x_media_core_key or not secrets.compare_digest(x_media_core_key, CORE_KEY):
        raise HTTPException(status_code=403, detail="Invalid local-core session key.")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "Media Downloader Local Core",
        "mode": "local-pwa",
        "version": CORE_VERSION,
    }


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    settings = SETTINGS.get()
    download_dir = Path(str(settings["download_dir"])).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)
    if bool(settings.get("auto_check_core_updates")) and UPDATER.snapshot().get("status") == "idle":
        UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
        UPDATER.start_check()
    return {
        "ok": True,
        "core_key": CORE_KEY,
        "core_version": CORE_VERSION,
        "download_dir": str(download_dir),
        "max_concurrent_downloads": int(settings["max_concurrent_downloads"]),
        "settings": settings,
        "update": UPDATER.snapshot(),
        "agent": _agent_snapshot(),
        "capabilities": {
            "analyze": True,
            "cancel_analysis": True,
            "download": True,
            "cancel_download": True,
            "retry_download": True,
            "local_save": True,
            "editor": True,
        },
    }


@app.post("/api/analyze", dependencies=[Depends(require_key)])
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    RATE_LIMITER.check("analysis", limit=30, window_seconds=60)
    job = MANAGER.start_analysis(payload.url.strip())
    return {"job": job.snapshot()}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_key)])
def job(job_id: str) -> dict[str, Any]:
    snapshot = MANAGER.snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"job": snapshot}


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_key)])
def cancel_job(job_id: str) -> dict[str, Any]:
    if not MANAGER.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job cannot be cancelled.")
    return {"ok": True}


@app.post("/api/downloads", dependencies=[Depends(require_key)])
def create_download(payload: DownloadRequest) -> dict[str, Any]:
    RATE_LIMITER.check("download", limit=60, window_seconds=60)
    settings = SETTINGS.get()
    directory = Path(payload.download_dir or str(settings["download_dir"])).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Download folder is not writable: {exc}") from exc

    try:
        job = MANAGER.start_download(
            analysis_id=payload.analysis_id,
            mode=payload.mode,
            video_quality=payload.video_quality,
            audio_format=payload.audio_format,
            audio_quality=payload.audio_quality,
            filename=payload.filename,
            download_dir=str(directory),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@app.get("/api/downloads", dependencies=[Depends(require_key)])
def downloads() -> dict[str, Any]:
    return {"downloads": MANAGER.list_kind("download")}


@app.post("/api/downloads/{job_id}/retry", dependencies=[Depends(require_key)])
def retry_download(job_id: str) -> dict[str, Any]:
    RATE_LIMITER.check("download-retry", limit=30, window_seconds=60)
    try:
        job = MANAGER.retry_download(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@app.delete("/api/downloads/{job_id}", dependencies=[Depends(require_key)])
def cancel_download(job_id: str) -> dict[str, Any]:
    if not MANAGER.cancel(job_id):
        raise HTTPException(status_code=409, detail="Download cannot be cancelled.")
    return {"ok": True}


@app.post("/api/system/open-downloads", dependencies=[Depends(require_key)])
def open_downloads(payload: ConfigRequest) -> dict[str, Any]:
    directory = Path(payload.download_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(directory))
        return {"ok": True}
    raise HTTPException(status_code=501, detail="Open folder is currently implemented for Windows.")


@app.post("/api/system/choose-folder", dependencies=[Depends(require_key)])
def choose_folder(payload: FolderPickerRequest) -> dict[str, Any]:
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Native folder picker is currently implemented for Windows.")

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        initial = (
            str(Path(payload.current_dir).expanduser())
            if payload.current_dir
            else str(Path(str(SETTINGS.get()["download_dir"])).expanduser())
        )
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=initial if Path(initial).exists() else str(Path.home()),
            title="Choose Media Downloader save folder",
            mustexist=True,
        )
        root.destroy()
    except Exception as exc:
        LOGGER.exception("folder_picker_failed")
        raise HTTPException(status_code=500, detail=f"Could not open the folder picker: {exc}") from exc

    return {"selected": str(selected or "")}


def _diagnostics_snapshot() -> dict[str, Any]:
    downloads = MANAGER.list_kind("download", limit=120)
    counts: dict[str, int] = {}
    for item in downloads:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    browser = find_browser()
    settings = SETTINGS.get()
    download_dir = Path(str(settings["download_dir"])).expanduser()
    try:
        disk = shutil.disk_usage(download_dir if download_dir.exists() else download_dir.parent)
        disk_free = int(disk.free)
        disk_total = int(disk.total)
    except OSError:
        disk_free = 0
        disk_total = 0

    return {
        "version": CORE_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "download_dir": str(settings["download_dir"]),
        "state_file": str(STATE_FILE),
        "settings_file": str(SETTINGS_FILE),
        "editor_library_file": str(EDITOR_LIBRARY_FILE),
        "log_file": str(LOG_FILE),
        "update_dir": str(UPDATE_DIR),
        "diagnostics_dir": str(DIAGNOSTICS_DIR),
        "browser": str(browser) if browser else "",
        "ffmpeg": str(get_ffmpeg_exe()),
        "network": safe_proxy_label(),
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "download_counts": counts,
        "settings": settings,
        "update": UPDATER.snapshot(),
        "agent": _agent_snapshot(),
        "active_work": MANAGER.active_summary(),
    }


@app.get("/api/diagnostics", dependencies=[Depends(require_key)])
def diagnostics() -> dict[str, Any]:
    return _diagnostics_snapshot()


@app.get("/api/diagnostics/bundle", dependencies=[Depends(require_key)])
def diagnostics_bundle() -> FileResponse:
    RATE_LIMITER.check("diagnostics-bundle", limit=4, window_seconds=60)
    bundle = create_diagnostics_bundle(
        output_dir=DIAGNOSTICS_DIR,
        log_file=LOG_FILE,
        settings=SETTINGS.get(),
        jobs=MANAGER.list_kind("download", limit=120) + MANAGER.list_kind("editor_export", limit=80),
        diagnostics=_diagnostics_snapshot(),
        app_data_dir=APP_DATA_DIR,
    )
    return FileResponse(
        bundle,
        media_type="application/zip",
        filename=bundle.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/editor/choose-file", dependencies=[Depends(require_key)])
def choose_editor_file() -> dict[str, Any]:
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Native media picker is currently implemented for Windows.")

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            parent=root,
            title="Choose video or audio to edit",
            filetypes=[
                ("Media files", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi *.mp3 *.m4a *.wav *.aac *.ogg *.opus *.flac"),
                ("Video", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi"),
                ("Audio", "*.mp3 *.m4a *.wav *.aac *.ogg *.opus *.flac"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
    except Exception as exc:
        LOGGER.exception("editor_file_picker_failed")
        raise HTTPException(status_code=500, detail=f"Could not open the media picker: {exc}") from exc

    if not selected:
        return {"selected": ""}
    return _editor_probe_payload(Path(selected))


def _editor_probe_payload(source: Path) -> dict[str, Any]:
    source = source.expanduser()
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Media file does not exist.")
    try:
        info = probe_media(source)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "selected": str(source),
        "path": str(source),
        "filename": source.name,
        "stem": source.stem,
        "output_name": safe_export_name(f"{source.stem}_edited"),
        "output_dir": str(source.parent),
        "info": public_media_info(info),
    }


@app.post("/api/editor/probe", dependencies=[Depends(require_key)])
def editor_probe(payload: EditorPathRequest) -> dict[str, Any]:
    return _editor_probe_payload(Path(payload.path))


@app.post("/api/editor/preview", dependencies=[Depends(require_key)])
def editor_preview(payload: EditorPreviewRequest) -> Response:
    try:
        image = extract_preview_frame(
            Path(payload.path),
            payload.position,
            crop_preset=payload.crop_preset,
            rotate=payload.rotate,
            custom_crop=_custom_crop(payload),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/editor/proxy", dependencies=[Depends(require_key)])
def editor_proxy(payload: EditorProxyRequest) -> Response:
    try:
        clip = render_proxy_clip(
            Path(payload.path),
            start=payload.position,
            duration=payload.duration,
            crop_preset=payload.crop_preset,
            custom_crop=_custom_crop(payload),
            rotate=payload.rotate,
            speed=payload.speed,
            mute=payload.mute,
            volume_percent=payload.volume_percent,
            audio_preset=payload.audio_preset,
            noise_reduction=payload.noise_reduction,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=clip, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.post("/api/editor/waveform", dependencies=[Depends(require_key)])
def editor_waveform(payload: EditorPathRequest) -> Response:
    try:
        image = extract_waveform(Path(payload.path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/editor/exports", dependencies=[Depends(require_key)])
def editor_export(payload: EditorExportRequest) -> dict[str, Any]:
    RATE_LIMITER.check("editor-export", limit=20, window_seconds=60)
    source = Path(payload.path).expanduser()
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Media file does not exist.")

    output_dir = Path(payload.output_dir).expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Export folder is not writable: {exc}") from exc

    request = payload.model_dump()
    request["source_path"] = request.pop("path")
    job = MANAGER.start_editor_export(request)
    return {"job": job.snapshot()}


@app.get("/api/editor/exports", dependencies=[Depends(require_key)])
def editor_exports() -> dict[str, Any]:
    return {"exports": MANAGER.list_kind("editor_export", limit=30)}


@app.get("/api/editor/library", dependencies=[Depends(require_key)])
def editor_library() -> dict[str, Any]:
    return EDITOR_LIBRARY.snapshot()


@app.post("/api/editor/presets", dependencies=[Depends(require_key)])
def save_editor_preset(payload: EditorLibrarySave) -> dict[str, Any]:
    try:
        item = EDITOR_LIBRARY.save_preset(payload.name, payload.data, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"preset": item, **EDITOR_LIBRARY.snapshot()}


@app.delete("/api/editor/presets/{item_id}", dependencies=[Depends(require_key)])
def delete_editor_preset(item_id: str) -> dict[str, Any]:
    if not EDITOR_LIBRARY.delete_preset(item_id):
        raise HTTPException(status_code=404, detail="Editor preset not found.")
    return {"ok": True, **EDITOR_LIBRARY.snapshot()}


@app.post("/api/editor/projects", dependencies=[Depends(require_key)])
def save_editor_project(payload: EditorLibrarySave) -> dict[str, Any]:
    try:
        item = EDITOR_LIBRARY.save_project(payload.name, payload.data, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": item, **EDITOR_LIBRARY.snapshot()}


@app.delete("/api/editor/projects/{item_id}", dependencies=[Depends(require_key)])
def delete_editor_project(item_id: str) -> dict[str, Any]:
    if not EDITOR_LIBRARY.delete_project(item_id):
        raise HTTPException(status_code=404, detail="Editor project not found.")
    return {"ok": True, **EDITOR_LIBRARY.snapshot()}


@app.post("/api/editor/exports/{job_id}/retry", dependencies=[Depends(require_key)])
def retry_editor_export(job_id: str) -> dict[str, Any]:
    try:
        job = MANAGER.retry_editor_export(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@app.get("/api/settings", dependencies=[Depends(require_key)])
def get_settings() -> dict[str, Any]:
    return {"settings": SETTINGS.get()}


@app.put("/api/settings", dependencies=[Depends(require_key)])
def update_settings(payload: SettingsPatch) -> dict[str, Any]:
    patch = payload.model_dump(exclude_none=True)
    before = SETTINGS.get()
    settings, restart_required = SETTINGS.update(patch)

    try:
        Path(str(settings["download_dir"])).expanduser().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        SETTINGS.update({"download_dir": before["download_dir"]})
        raise HTTPException(status_code=400, detail=f"Download folder is not writable: {exc}") from exc

    if "launch_at_login" in patch:
        requested_startup = bool(settings.get("launch_at_login"))
        if can_manage_startup():
            try:
                set_launch_at_login(requested_startup)
            except Exception as exc:
                SETTINGS.update({"launch_at_login": before.get("launch_at_login", False)})
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif requested_startup:
            SETTINGS.update({"launch_at_login": before.get("launch_at_login", False)})
            raise HTTPException(
                status_code=409,
                detail="Launch at Windows sign-in is available after installing Media Downloader Core.",
            )

    UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
    LOGGER.info("settings_updated keys=%s restart_required=%s", sorted(patch.keys()), restart_required)
    return {
        "settings": settings,
        "restart_required": restart_required,
        "active_max_concurrent_downloads": int(CURRENT_SETTINGS["max_concurrent_downloads"]),
    }


@app.get("/api/agent/status", dependencies=[Depends(require_key)])
def agent_status() -> dict[str, Any]:
    return _agent_snapshot()


@app.post("/api/agent/open", dependencies=[Depends(require_key)])
def agent_open() -> dict[str, Any]:
    try:
        return send_control("open_app")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/restart", dependencies=[Depends(require_key)])
def agent_restart() -> dict[str, Any]:
    try:
        return send_control("restart")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/quit", dependencies=[Depends(require_key)])
def agent_quit() -> dict[str, Any]:
    try:
        return send_control("quit")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/rollback", dependencies=[Depends(require_key)])
def agent_rollback() -> dict[str, Any]:
    agent = _agent_snapshot()
    if not agent.get("rollback_available"):
        raise HTTPException(status_code=409, detail="No previous Local Core version is available.")
    if MANAGER.has_active_work():
        raise HTTPException(status_code=409, detail="Wait for active downloads/exports to finish before rolling back.")
    try:
        return send_control("rollback_previous")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/update/status", dependencies=[Depends(require_key)])
def update_status() -> dict[str, Any]:
    return {"update": UPDATER.snapshot()}


@app.post("/api/update/check", dependencies=[Depends(require_key)])
def check_update() -> dict[str, Any]:
    RATE_LIMITER.check("update-check", limit=10, window_seconds=60)
    settings = SETTINGS.get()
    UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
    return {"update": UPDATER.start_check()}


@app.post("/api/update/download", dependencies=[Depends(require_key)])
def download_update() -> dict[str, Any]:
    RATE_LIMITER.check("update-download", limit=5, window_seconds=60)
    try:
        return {"update": UPDATER.start_download()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/update/apply", dependencies=[Depends(require_key)])
def apply_update() -> dict[str, Any]:
    RATE_LIMITER.check("update-apply", limit=3, window_seconds=60)
    state = UPDATER.snapshot()
    staged = str(state.get("staged_path") or "")
    if state.get("status") != "ready" or not staged:
        raise HTTPException(status_code=409, detail="No verified Local Core update is ready to apply.")
    if not can_self_update():
        raise HTTPException(status_code=409, detail="Update apply requires the installed Local Core agent.")
    if MANAGER.has_active_work():
        raise HTTPException(status_code=409, detail="Wait for active downloads/exports to finish before applying the update.")
    try:
        send_control(
            "apply_update",
            path=staged,
            expected_version=str(state.get("latest_version") or ""),
            updater_path=str(state.get("staged_updater_path") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "detail": "Applying verified update. Local Core will restart."}


_AUTO_UPDATE_LOCK = threading.Lock()
_AUTO_UPDATE_STARTED = False
_AUTO_UPDATE_LAST_CHECK = 0.0


def _start_unattended_update_worker() -> None:
    global _AUTO_UPDATE_STARTED
    with _AUTO_UPDATE_LOCK:
        if _AUTO_UPDATE_STARTED:
            return
        _AUTO_UPDATE_STARTED = True
    threading.Thread(
        target=_unattended_update_loop,
        daemon=True,
        name="local-core-unattended-updater",
    ).start()


def _unattended_update_loop() -> None:
    global _AUTO_UPDATE_LAST_CHECK
    time.sleep(4.0)
    while True:
        try:
            settings = SETTINGS.get()
            if not bool(settings.get("auto_check_core_updates", True)):
                time.sleep(60.0)
                continue

            policy = str(settings.get("update_policy") or "notify")
            state = UPDATER.snapshot()
            status = str(state.get("status") or "idle")
            now = time.time()

            should_check = status == "idle" or (
                status in {"current", "error"}
                and now - _AUTO_UPDATE_LAST_CHECK >= 6 * 60 * 60
            )
            if should_check:
                UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
                UPDATER.start_check()
                _AUTO_UPDATE_LAST_CHECK = now
                time.sleep(15.0)
                continue

            if policy in {"download", "install"} and status == "available":
                try:
                    UPDATER.start_download()
                except ValueError:
                    pass
                time.sleep(15.0)
                continue

            if policy == "install" and status == "ready" and bool(state.get("can_apply")):
                install_hour = int(settings.get("update_install_hour", 3))
                if time.localtime().tm_hour == install_hour and not MANAGER.has_active_work():
                    staged = str(state.get("staged_path") or "")
                    if staged:
                        LOGGER.info(
                            "unattended_core_update_apply version=%s",
                            state.get("latest_version"),
                        )
                        send_control(
                            "apply_update",
                            path=staged,
                            expected_version=str(state.get("latest_version") or ""),
                            updater_path=str(state.get("staged_updater_path") or ""),
                        )
                        return
        except Exception:
            LOGGER.exception("unattended_core_update_cycle_failed")
        time.sleep(60.0)


@app.on_event("startup")
def start_background_services() -> None:
    _start_unattended_update_worker()


@app.post("/api/system/open-log", dependencies=[Depends(require_key)])
def open_log() -> dict[str, Any]:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)
    if os.name == "nt":
        os.startfile(str(LOG_FILE))
        return {"ok": True}
    raise HTTPException(status_code=501, detail="Open log is currently implemented for Windows.")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="pwa")
