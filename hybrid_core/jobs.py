from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_core.editor import EditorCancelled, run_export

from .engine import Cancelled, analyze_url, download_from_analysis
from .state_store import JsonStateStore


TERMINAL = {"completed", "failed", "cancelled"}


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    request: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    private: dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "detail": self.detail,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": {
                key: value
                for key, value in self.request.items()
                if key not in {"cached_info"}
            },
            "result": self.result,
        }


class JobManager:
    def __init__(
        self,
        *,
        max_downloads: int = 3,
        state_path: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._analysis_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-analysis")
        self._download_pool = ThreadPoolExecutor(
            max_workers=max(1, min(6, int(max_downloads))),
            thread_name_prefix="hybrid-download",
        )
        self._editor_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hybrid-editor")
        self._store = JsonStateStore(state_path) if state_path is not None else None
        self._logger = logger or logging.getLogger("media_downloader.hybrid")
        self._restore()

    def _restore(self) -> None:
        if self._store is None:
            return
        restored = 0
        for item in self._store.load():
            kind = str(item.get("kind") or "")
            if kind not in {"download", "editor_export"}:
                continue
            status = str(item.get("status") or "failed")
            if status in {"queued", "running", "cancelling"}:
                status = "failed"
                item["detail"] = "Interrupted by Local Core restart"
                item["error"] = (
                    "The previous Local Core session ended before this job finished. "
                    "Press Retry to run it again."
                )
            job = Job(
                id=str(item.get("id") or uuid.uuid4().hex),
                kind=kind,
                status=status,
                progress=float(item.get("progress") or 0.0),
                detail=str(item.get("detail") or ""),
                error=str(item.get("error") or ""),
                created_at=float(item.get("created_at") or time.time()),
                updated_at=float(item.get("updated_at") or time.time()),
                request=dict(item.get("request") or {}),
                result=dict(item.get("result") or {}),
            )
            self._jobs[job.id] = job
            restored += 1
        if restored:
            self._logger.info("restored_persistent_jobs count=%s", restored)
            self._persist()

    def _persist(self) -> None:
        if self._store is None:
            return
        with self._lock:
            persistent = [
                job.snapshot()
                for job in sorted(self._jobs.values(), key=lambda item: item.created_at)[-180:]
                if job.kind in {"download", "editor_export"}
            ]
        try:
            self._store.save(persistent)
        except Exception:
            self._logger.exception("could_not_persist_jobs")

    def _new(self, kind: str, request: dict[str, Any]) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, request=dict(request))
        with self._lock:
            self._jobs[job.id] = job
        if kind in {"download", "editor_export"}:
            self._persist()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        with self._lock:
            return job.snapshot()

    def list_kind(self, kind: str, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.kind == kind]
            jobs.sort(key=lambda item: item.created_at, reverse=True)
            return [job.snapshot() for job in jobs[:limit]]

    def _set(
        self,
        job: Job,
        *,
        status: str | None = None,
        progress: float | None = None,
        detail: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0.0, min(1.0, float(progress)))
            if detail is not None:
                job.detail = detail
            if error is not None:
                job.error = error
            if result is not None:
                job.result = result
            job.updated_at = time.time()
        if job.kind in {"download", "editor_export"}:
            self._persist()

    def start_analysis(self, url: str) -> Job:
        job = self._new("analysis", {"url": url})
        self._analysis_pool.submit(self._run_analysis, job)
        return job

    def _run_analysis(self, job: Job) -> None:
        self._set(job, status="running", detail="Starting analysis")
        try:
            public, cached = analyze_url(
                str(job.request.get("url") or ""),
                cancel_event=job.cancel_event,
                on_status=lambda text: self._set(job, detail=text),
            )
            job.private["cached_info"] = cached
            self._set(job, status="completed", progress=1.0, detail="Ready", result=public)
        except Cancelled:
            self._set(job, status="cancelled", detail="Analysis cancelled")
        except Exception as exc:
            self._set(job, status="failed", detail="Analysis failed", error=str(exc))

    def start_download(
        self,
        *,
        analysis_id: str,
        mode: str,
        video_quality: str,
        audio_format: str,
        audio_quality: str,
        filename: str,
        download_dir: str,
    ) -> Job:
        analysis = self.get(analysis_id)
        if analysis is None or analysis.kind != "analysis" or analysis.status != "completed":
            raise ValueError("Analysis is not ready.")
        cached_info = analysis.private.get("cached_info")
        if not isinstance(cached_info, dict):
            raise ValueError("Analyzed media data is unavailable.")

        request = {
            "analysis_id": analysis_id,
            "url": str(analysis.request.get("url") or ""),
            "mode": mode,
            "video_quality": video_quality,
            "audio_format": audio_format,
            "audio_quality": audio_quality,
            "filename": filename,
            "download_dir": download_dir,
        }
        job = self._new("download", request)
        job.private["cached_info"] = cached_info
        self._download_pool.submit(self._run_download, job)
        return job

    def _run_download(self, job: Job) -> None:
        self._set(job, status="running", detail="Starting download")
        try:
            cached_info = job.private.get("cached_info")
            if not isinstance(cached_info, dict) or not cached_info:
                self._set(job, detail="Refreshing media information")
                _public, cached_info = analyze_url(
                    str(job.request.get("url") or ""),
                    cancel_event=job.cancel_event,
                    on_status=lambda text: self._set(job, detail=f"Refresh: {text}"),
                )
                job.private["cached_info"] = cached_info

            output = download_from_analysis(
                url=str(job.request.get("url") or ""),
                cached_info=dict(cached_info or {}),
                download_dir=Path(str(job.request.get("download_dir") or "")),
                filename=str(job.request.get("filename") or "media_download"),
                mode=str(job.request.get("mode") or "Video"),
                video_quality=str(job.request.get("video_quality") or "Best available"),
                audio_format=str(job.request.get("audio_format") or "MP3"),
                audio_quality=str(job.request.get("audio_quality") or "192"),
                cancel_event=job.cancel_event,
                on_status=lambda text: self._set(job, detail=text),
                on_progress=lambda value, text: self._set(job, progress=value, detail=text),
            )
            self._set(
                job,
                status="completed",
                progress=1.0,
                detail="Download complete",
                result={"path": str(output), "filename": output.name},
            )
        except Cancelled:
            self._set(job, status="cancelled", detail="Download cancelled")
        except Exception as exc:
            self._set(job, status="failed", detail="Download failed", error=str(exc))

    def start_editor_export(self, request: dict[str, Any]) -> Job:
        job = self._new("editor_export", request)
        self._editor_pool.submit(self._run_editor_export, job)
        return job

    def _run_editor_export(self, job: Job) -> None:
        self._set(job, status="running", detail="Preparing export")
        try:
            crop_preset = str(job.request.get("crop_preset") or "Original")
            custom_crop = None
            if crop_preset == "Custom":
                custom_crop = (
                    int(job.request.get("custom_x") or 0),
                    int(job.request.get("custom_y") or 0),
                    int(job.request.get("custom_width") or 0),
                    int(job.request.get("custom_height") or 0),
                )

            output = run_export(
                Path(str(job.request.get("source_path") or "")),
                output_dir=Path(str(job.request.get("output_dir") or "")),
                output_name=str(job.request.get("output_name") or "edited_media"),
                start=float(job.request.get("start") or 0.0),
                end=float(job.request.get("end") or 0.0),
                crop_preset=crop_preset,
                custom_crop=custom_crop,
                rotate=str(job.request.get("rotate") or "0°"),
                speed=float(job.request.get("speed") or 1.0),
                mute=bool(job.request.get("mute")),
                volume_percent=float(job.request.get("volume_percent") or 100.0),
                fade_in=float(job.request.get("fade_in") or 0.0),
                fade_out=float(job.request.get("fade_out") or 0.0),
                audio_preset=str(job.request.get("audio_preset") or "Flat"),
                noise_reduction=bool(job.request.get("noise_reduction")),
                quality=str(job.request.get("quality") or "Balanced"),
                cancel_event=job.cancel_event,
                on_progress=lambda value, text: self._set(job, progress=value, detail=text),
                on_status=lambda text: self._set(job, detail=text),
            )
            self._set(
                job,
                status="completed",
                progress=1.0,
                detail="Export complete",
                result={"path": str(output), "filename": output.name},
            )
        except EditorCancelled:
            self._set(job, status="cancelled", detail="Export cancelled")
        except Exception as exc:
            self._logger.exception("editor_export_failed job=%s", job.id)
            self._set(job, status="failed", detail="Export failed", error=str(exc))

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in TERMINAL:
            return False
        job.cancel_event.set()
        self._set(job, status="cancelling", detail="Cancelling")
        return True

    def retry_editor_export(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None or job.kind != "editor_export" or job.status != "failed":
            raise ValueError("Only failed editor exports can be retried.")

        job.cancel_event = threading.Event()
        self._set(job, status="queued", progress=0.0, detail="Retry queued", error="", result={})
        self._editor_pool.submit(self._run_editor_export, job)
        return job

    def retry_download(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None or job.kind != "download" or job.status != "failed":
            raise ValueError("Only failed downloads can be retried.")

        job.cancel_event = threading.Event()
        self._set(job, status="queued", progress=0.0, detail="Retry queued", error="", result={})
        self._download_pool.submit(self._run_download, job)
        return job
