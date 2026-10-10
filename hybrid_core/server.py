from __future__ import annotations

import os
import platform
import secrets
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from imageio_ffmpeg import get_ffmpeg_exe

from media_core.browser_resolver import find_browser
from media_core.network import safe_proxy_label

from .jobs import JobManager
from .logging_setup import configure_logging
from .version import CORE_VERSION


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web_pwa"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Media Downloader"
APP_DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
STATE_FILE = APP_DATA_DIR / "hybrid-jobs.json"
LOG_FILE = APP_DATA_DIR / "hybrid-core.log"
CORE_KEY = secrets.token_urlsafe(32)
LOGGER = configure_logging(LOG_FILE)
MANAGER = JobManager(max_downloads=3, state_path=STATE_FILE, logger=LOGGER)
LOGGER.info("local_core_started version=%s", CORE_VERSION)

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
    DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "core_key": CORE_KEY,
        "core_version": CORE_VERSION,
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "max_concurrent_downloads": 3,
        "capabilities": {
            "analyze": True,
            "cancel_analysis": True,
            "download": True,
            "cancel_download": True,
            "retry_download": True,
            "local_save": True,
            "editor": False,
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
    directory = Path(payload.download_dir or DEFAULT_DOWNLOAD_DIR).expanduser()
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
        initial = str(Path(payload.current_dir).expanduser()) if payload.current_dir else str(DEFAULT_DOWNLOAD_DIR)
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
    return {
        "version": CORE_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "state_file": str(STATE_FILE),
        "log_file": str(LOG_FILE),
        "browser": str(browser) if browser else "",
        "ffmpeg": str(get_ffmpeg_exe()),
        "network": safe_proxy_label(),
        "download_counts": counts,
    }


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
