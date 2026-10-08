from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class DownloadRequest:
    id: str
    source_type: str
    payload: dict[str, Any]
    edit_after_download: bool = False
    capture_id: str = ""
    status: str = "queued"
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    def snapshot(self, *, position: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "edit_after_download": self.edit_after_download,
            "capture_id": self.capture_id,
            "status": self.status,
            "progress": self.progress,
            "detail": self.detail,
            "error": self.error,
            "position": position,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class DownloadQueue:
    """Single-worker FIFO queue for normal links and Browser Capture downloads."""

    def __init__(self, *, history_limit: int = 120) -> None:
        self.history_limit = max(20, int(history_limit))
        self._items: list[DownloadRequest] = []
        self._pending: deque[str] = deque()
        self._active_id: str | None = None

    def enqueue(
        self,
        source_type: str,
        payload: dict[str, Any],
        *,
        edit_after_download: bool = False,
        capture_id: str = "",
    ) -> DownloadRequest:
        request = DownloadRequest(
            id=uuid.uuid4().hex,
            source_type=str(source_type or "link"),
            payload=dict(payload or {}),
            edit_after_download=bool(edit_after_download),
            capture_id=str(capture_id or ""),
        )
        self._items.append(request)
        self._pending.append(request.id)
        self._trim_history()
        return request

    def start_next(self) -> DownloadRequest | None:
        if self._active_id is not None:
            return None
        while self._pending:
            request_id = self._pending.popleft()
            request = self._find(request_id)
            if request is None or request.status != "queued":
                continue
            request.status = "running"
            request.started_at = time.time()
            request.detail = "Starting…"
            self._active_id = request.id
            return request
        return None

    def active(self) -> DownloadRequest | None:
        return self._find(self._active_id) if self._active_id else None

    def update_progress(self, request_id: str, progress: float, detail: str = "") -> None:
        request = self._find(request_id)
        if request is None or request.status != "running":
            return
        request.progress = max(0.0, min(1.0, float(progress)))
        if detail:
            request.detail = str(detail)

    def complete(self, request_id: str, detail: str = "") -> None:
        self._finish(request_id, "completed", detail=detail)

    def fail(self, request_id: str, error: str) -> None:
        self._finish(request_id, "failed", error=error, detail="Download failed")

    def cancel(self, request_id: str) -> bool:
        request = self._find(request_id)
        if request is None or request.status in TERMINAL_STATUSES:
            return False
        if request.status == "queued":
            try:
                self._pending.remove(request.id)
            except ValueError:
                pass
        self._finish(request.id, "cancelled", detail="Cancelled")
        return True

    def cancel_capture(self, capture_id: str) -> str | None:
        capture_id = str(capture_id or "")
        for request in reversed(self._items):
            if request.capture_id != capture_id or request.status in TERMINAL_STATUSES:
                continue
            self.cancel(request.id)
            return request.id
        return None

    def latest_for_capture(self, capture_id: str) -> dict[str, Any] | None:
        capture_id = str(capture_id or "")
        for request in reversed(self._items):
            if request.capture_id == capture_id:
                return request.snapshot(position=self.position(request.id))
        return None

    def position(self, request_id: str) -> int:
        try:
            return list(self._pending).index(request_id) + 1
        except ValueError:
            return 0

    def queued_count(self) -> int:
        count = 0
        for request_id in self._pending:
            request = self._find(request_id)
            if request is not None and request.status == "queued":
                count += 1
        return count

    def running_count(self) -> int:
        return 1 if self.active() is not None else 0

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.snapshot(position=self.position(item.id)) for item in reversed(self._items)]

    def _finish(
        self,
        request_id: str,
        status: str,
        *,
        detail: str = "",
        error: str = "",
    ) -> None:
        request = self._find(request_id)
        if request is None:
            return
        request.status = status
        request.progress = 1.0 if status == "completed" else request.progress
        request.detail = detail or request.detail
        request.error = error
        request.finished_at = time.time()
        if self._active_id == request.id:
            self._active_id = None

    def _find(self, request_id: str | None) -> DownloadRequest | None:
        if not request_id:
            return None
        for request in reversed(self._items):
            if request.id == request_id:
                return request
        return None

    def _trim_history(self) -> None:
        if len(self._items) <= self.history_limit:
            return
        protected = set(self._pending)
        if self._active_id:
            protected.add(self._active_id)
        kept: list[DownloadRequest] = []
        removable = len(self._items) - self.history_limit
        for request in self._items:
            if removable > 0 and request.id not in protected and request.status in TERMINAL_STATUSES:
                removable -= 1
                continue
            kept.append(request)
        self._items = kept
