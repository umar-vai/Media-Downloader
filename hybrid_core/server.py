from __future__ import annotations

import os
import platform
import secrets
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from imageio_ffmpeg import get_ffmpeg_exe

from media_core.browser_resolver import find_browser
from media_core.editor import extract_preview_frame, extract_waveform, probe_media, public_media_info, render_proxy_clip, safe_export_name
from media_core.network import safe_proxy_label

from .core_updater import CoreUpdateService
from .editor_library import EditorLibrary
from .jobs import JobManager
from .logging_setup import configure_logging
from .paths import (
    APP_DATA_DIR,
    DEFAULT_DOWNLOAD_DIR,
    EDITOR_LIBRARY_FILE,
    LOG_FILE,
    SETTINGS_FILE,
    STATE_FILE,
    UPDATE_DIR,
    WEB_DIR,
)
from .settings_store import SettingsStore
from .version import CORE_VERSION


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
    open_browser_on_start: bool | None = None


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


@app.get("/api/diagnostics", dependencies=[Depends(require_key)])
def diagnostics() -> dict[str, Any]:
    downloads = MANAGER.list_kind("download", limit=120)
    counts: dict[str, int] = {}
    for item in downloads:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    browser = find_browser()
    settings = SETTINGS.get()
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
        "browser": str(browser) if browser else "",
        "ffmpeg": str(get_ffmpeg_exe()),
        "network": safe_proxy_label(),
        "download_counts": counts,
        "settings": settings,
        "update": UPDATER.snapshot(),
    }


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

    UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
    LOGGER.info("settings_updated keys=%s restart_required=%s", sorted(patch.keys()), restart_required)
    return {
        "settings": settings,
        "restart_required": restart_required,
        "active_max_concurrent_downloads": int(CURRENT_SETTINGS["max_concurrent_downloads"]),
    }


@app.get("/api/update/status", dependencies=[Depends(require_key)])
def update_status() -> dict[str, Any]:
    return {"update": UPDATER.snapshot()}


@app.post("/api/update/check", dependencies=[Depends(require_key)])
def check_update() -> dict[str, Any]:
    settings = SETTINGS.get()
    UPDATER.set_channel(str(settings.get("update_channel") or "stable"))
    return {"update": UPDATER.start_check()}


@app.post("/api/update/download", dependencies=[Depends(require_key)])
def download_update() -> dict[str, Any]:
    try:
        return {"update": UPDATER.start_download()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
