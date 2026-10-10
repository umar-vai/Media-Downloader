from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class DownloadRequest:
    id: str
    payload: dict[str, Any]
    edit_after_download: bool = False
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
            "name": str(self.payload.get("name") or "Media download"),
            "url": str(self.payload.get("url") or ""),
            "edit_after_download": self.edit_after_download,
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
    """Thread-safe queue that can run several pasted-link downloads concurrently."""

    def __init__(self, *, history_limit: int = 120, max_concurrent: int = 3) -> None:
        self.history_limit = max(20, int(history_limit))
        self.max_concurrent = max(1, min(6, int(max_concurrent)))
        self._items: list[DownloadRequest] = []
        self._pending: deque[str] = deque()
        self._active_ids: set[str] = set()
        self._lock = threading.RLock()

    def enqueue(
        self,
        payload: dict[str, Any],
        *,
        edit_after_download: bool = False,
    ) -> DownloadRequest:
        with self._lock:
            request = DownloadRequest(
                id=uuid.uuid4().hex,
                payload=dict(payload or {}),
                edit_after_download=bool(edit_after_download),
            )
            self._items.append(request)
            self._pending.append(request.id)
            self._trim_history_locked()
            return request

    def start_available(self) -> list[DownloadRequest]:
        started: list[DownloadRequest] = []
        with self._lock:
            while self._pending and len(self._active_ids) < self.max_concurrent:
                request_id = self._pending.popleft()
                request = self._find_locked(request_id)
                if request is None or request.status != "queued":
                    continue
                request.status = "running"
                request.started_at = time.time()
                request.detail = "Starting…"
                self._active_ids.add(request.id)
                started.append(request)
        return started

    def active(self) -> DownloadRequest | None:
        with self._lock:
            for request in reversed(self._items):
                if request.id in self._active_ids:
                    return request
        return None

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None:
                return None
            return request.snapshot(position=self._position_locked(request.id))

    def update_progress(self, request_id: str, progress: float, detail: str = "") -> None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status not in {"running", "cancelling"}:
                return
            request.progress = max(0.0, min(1.0, float(progress)))
            if detail:
                request.detail = str(detail)

    def update_detail(self, request_id: str, detail: str) -> None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in TERMINAL_STATUSES:
                return
            request.detail = str(detail or request.detail)

    def complete(self, request_id: str, detail: str = "") -> None:
        with self._lock:
            self._finish_locked(request_id, "completed", detail=detail)

    def fail(self, request_id: str, error: str) -> None:
        with self._lock:
            self._finish_locked(request_id, "failed", error=error, detail="Download failed")

    def request_cancel(self, request_id: str) -> str:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in TERMINAL_STATUSES:
                return request.status if request else "missing"
            if request.status == "queued":
                try:
                    self._pending.remove(request.id)
                except ValueError:
                    pass
                self._finish_locked(request.id, "cancelled", detail="Cancelled")
                return "cancelled"
            if request.status == "running":
                request.status = "cancelling"
                request.detail = "Cancelling…"
                return "cancelling"
            return request.status

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None:
                return False
            if request.status == "queued":
                try:
                    self._pending.remove(request.id)
                except ValueError:
                    pass
            self._finish_locked(request.id, "cancelled", detail="Cancelled")
            return True

    def position(self, request_id: str) -> int:
        with self._lock:
            return self._position_locked(request_id)

    def queued_count(self) -> int:
        with self._lock:
            return sum(
                1
                for request_id in self._pending
                if (request := self._find_locked(request_id)) is not None and request.status == "queued"
            )

    def running_count(self) -> int:
        with self._lock:
            return len(self._active_ids)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                item.snapshot(position=self._position_locked(item.id))
                for item in reversed(self._items)
            ]

    def _position_locked(self, request_id: str) -> int:
        try:
            return list(self._pending).index(request_id) + 1
        except ValueError:
            return 0

    def _finish_locked(
        self,
        request_id: str,
        status: str,
        *,
        detail: str = "",
        error: str = "",
    ) -> None:
        request = self._find_locked(request_id)
        if request is None:
            return
        request.status = status
        request.progress = 1.0 if status == "completed" else request.progress
        request.detail = detail or request.detail
        request.error = error
        request.finished_at = time.time()
        self._active_ids.discard(request.id)

    def _find_locked(self, request_id: str | None) -> DownloadRequest | None:
        if not request_id:
            return None
        for request in reversed(self._items):
            if request.id == request_id:
                return request
        return None

    def _trim_history_locked(self) -> None:
        if len(self._items) <= self.history_limit:
            return
        protected = set(self._pending) | set(self._active_ids)
        kept: list[DownloadRequest] = []
        removable = len(self._items) - self.history_limit
        for request in self._items:
            if removable > 0 and request.id not in protected and request.status in TERMINAL_STATUSES:
                removable -= 1
                continue
            kept.append(request)
        self._items = kept
