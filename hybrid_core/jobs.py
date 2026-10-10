from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engine import Cancelled, analyze_url, download_from_analysis


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
    def __init__(self, *, max_downloads: int = 3) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._analysis_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-analysis")
        self._download_pool = ThreadPoolExecutor(
            max_workers=max(1, min(6, int(max_downloads))),
            thread_name_prefix="hybrid-download",
        )

    def _new(self, kind: str, request: dict[str, Any]) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, request=dict(request))
        with self._lock:
            self._jobs[job.id] = job
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
            output = download_from_analysis(
                url=str(job.request.get("url") or ""),
                cached_info=dict(job.private.get("cached_info") or {}),
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

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in TERMINAL:
            return False
        job.cancel_event.set()
        self._set(job, status="cancelling", detail="Cancelling")
        return True

    def retry_download(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None or job.kind != "download" or job.status != "failed":
            raise ValueError("Only failed downloads can be retried.")

        job.cancel_event = threading.Event()
        self._set(job, status="queued", progress=0.0, detail="Retry queued", error="", result={})
        self._download_pool.submit(self._run_download, job)
        return job
