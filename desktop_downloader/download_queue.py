from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MAX_BATCH_ITEMS = 300
MAX_OUTSTANDING = 600
_RATE_LIMIT_RE = re.compile(r"429|too many requests|rate.?limit", re.IGNORECASE)
RATE_LIMIT_COOLDOWN = 120.0
RATE_LIMIT_CONCURRENCY = 2


@dataclass
class DownloadRequest:
    id: str
    payload: dict[str, Any]
    edit_after_download: bool = False
    status: str = "queued"
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    result_path: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    def snapshot(self, *, position: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": str(self.payload.get("name") or "Media download"),
            "url": str(self.payload.get("url") or ""),
            "batch_id": str(self.payload.get("batch_id") or ""),
            "batch_index": int(self.payload.get("batch_index") or 0),
            "edit_after_download": self.edit_after_download,
            "status": self.status,
            "progress": self.progress,
            "detail": self.detail,
            "error": self.error,
            "last_error": str(self.payload.get("last_error") or self.error or ""),
            "auto_attempts": int(self.payload.get("auto_attempts") or 0),
            "cookiefile": str((self.payload.get("request_settings") or {}).get("cookiefile") or ""),
            "result_path": self.result_path,
            "position": position,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class DownloadQueue:
    """Thread-safe queue that can run several pasted-link downloads concurrently."""

    def __init__(self, *, history_limit: int = 120, max_concurrent: int = 6) -> None:
        self.history_limit = max(20, int(history_limit))
        self.max_concurrent = max(1, min(10, int(max_concurrent)))
        self._items: list[DownloadRequest] = []
        self._pending: deque[str] = deque()
        self._active_ids: set[str] = set()
        self._lock = threading.RLock()
        self._throttle_until = 0.0

    def note_error(self, error: str) -> None:
        """Slow the queue down for a while when a site answers 'too many requests'."""
        if _RATE_LIMIT_RE.search(str(error or "")):
            with self._lock:
                self._throttle_until = time.time() + RATE_LIMIT_COOLDOWN

    @property
    def throttled(self) -> bool:
        return time.time() < self._throttle_until

    def effective_concurrency(self) -> int:
        return min(self.max_concurrent, RATE_LIMIT_CONCURRENCY) if self.throttled else self.max_concurrent

    def update_url(self, request_id: str, url: str) -> bool:
        """Replace the link of a failed/queued item (used by 'edit and retry')."""
        url = str(url or "").strip()
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in {"running", "cancelling"} or not url:
                return False
            if url != str(request.payload.get("url") or ""):
                request.payload["url"] = url
                request.payload["cached_info"] = None
                settings = request.payload.get("request_settings")
                if isinstance(settings, dict):
                    settings["source_url"] = url
            return True

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

    def enqueue_batch(self, payloads: list[dict[str, Any]]) -> list[DownloadRequest]:
        """Enqueue a batch atomically, preserving submitted order."""
        if not payloads or len(payloads) > MAX_BATCH_ITEMS:
            raise ValueError(f"Batch must contain between 1 and {MAX_BATCH_ITEMS} items.")
        with self._lock:
            outstanding = self.queued_count() + self.running_count()
            if outstanding + len(payloads) > MAX_OUTSTANDING:
                raise ValueError(f"Download queue is full ({MAX_OUTSTANDING} pending/running items maximum).")
            requests = [self.enqueue(payload, edit_after_download=False) for payload in payloads]
        return requests

    def update_name(self, request_id: str, name: str) -> None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is not None and request.status in {"running", "cancelling"}:
                request.payload["name"] = str(name)

    def start_available(self) -> list[DownloadRequest]:
        started: list[DownloadRequest] = []
        with self._lock:
            while self._pending and len(self._active_ids) < self.effective_concurrency():
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
            # Never let the bar move backwards (e.g. after an automatic retry restarts a stream).
            request.progress = max(request.progress, max(0.0, min(1.0, float(progress))))
            if detail:
                request.detail = str(detail)

    def update_detail(self, request_id: str, detail: str) -> None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in TERMINAL_STATUSES:
                return
            request.detail = str(detail or request.detail)

    def complete(self, request_id: str, detail: str = "", path: str = "") -> None:
        with self._lock:
            self._finish_locked(request_id, "completed", detail=detail)
            request = self._find_locked(request_id)
            if request is not None and path:
                request.result_path = str(path)

    def clear_finished(self) -> int:
        """Drop completed/failed/cancelled entries from the list. Returns how many were removed."""
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.status not in TERMINAL_STATUSES]
            return before - len(self._items)

    def fail(self, request_id: str, error: str) -> None:
        with self._lock:
            request = self._find_locked(request_id)
            if request is not None:
                request.payload["last_error"] = str(error or "")
            self.note_error(error)
            self._finish_locked(request_id, "failed", error=error, detail="Download failed")

    def schedule_auto_retry(self, request_id: str, error: str) -> float | None:
        """Keep a failed batch item alive as 'queued' and return the backoff delay in seconds.

        Returns None when the item is not a batch item (or is gone), so the caller marks it failed.
        The item is not in the pending deque until `release_auto_retry` is called.
        """
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or not request.payload.get("batch_id"):
                return None
            attempts = int(request.payload.get("auto_attempts") or 0) + 1
            request.payload["auto_attempts"] = attempts
            delay = float(min(300, 10 * (2 ** min(attempts - 1, 5))))
            request.status = "queued"
            request.progress = 0.0
            request.error = ""
            request.payload["last_error"] = str(error or "")
            self.note_error(error)
            if _RATE_LIMIT_RE.search(str(error or "")):
                delay = max(delay, 60.0)
            reason = " ".join(str(error or "").split())[:90]
            request.detail = f"Auto-retry #{attempts} in {int(delay)}s" + (f" • {reason}" if reason else "")
            request.finished_at = 0.0
            self._active_ids.discard(request.id)
            return delay

    def release_auto_retry(self, request_id: str) -> bool:
        """Put a backing-off item into the pending queue, unless it was cancelled/paused meanwhile."""
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status != "queued" or request.id in self._pending:
                return False
            request.detail = "Retrying…"
            self._pending.append(request.id)
            return True

    def pause(self, request_id: str) -> str:
        """Pause a queued download now, or flag a running one so the worker can stop it."""
        with self._lock:
            request = self._find_locked(request_id)
            if request is None:
                return "missing"
            if request.status == "queued":
                try:
                    self._pending.remove(request.id)
                except ValueError:
                    pass
                request.status = "paused"
                request.detail = "Paused"
                return "paused"
            if request.status == "running":
                request.status = "cancelling"
                request.detail = "Pausing…"
                return "pausing"
            return request.status

    def mark_paused(self, request_id: str) -> None:
        """A running download stopped because of a pause request; keep partial data for resume."""
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in TERMINAL_STATUSES:
                return
            request.status = "paused"
            request.detail = "Paused — will resume where it stopped"
            self._active_ids.discard(request.id)

    def resume(self, request_id: str) -> bool:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status != "paused":
                return False
            request.status = "queued"
            request.detail = "Resuming…"
            self._pending.append(request.id)
            return True

    def retry(self, request_id: str) -> bool:
        """Move a failed or cancelled download back to the queue using its original payload."""
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status not in {"failed", "cancelled"}:
                return False
            if request.id in self._active_ids or request.id in self._pending:
                return False

            request.status = "queued"
            request.progress = 0.0
            request.detail = "Retry queued"
            request.error = ""
            request.result_path = ""
            request.started_at = 0.0
            request.finished_at = 0.0
            self._pending.append(request.id)
            return True

    def request_cancel(self, request_id: str) -> str:
        with self._lock:
            request = self._find_locked(request_id)
            if request is None or request.status in TERMINAL_STATUSES:
                return request.status if request else "missing"
            if request.status in {"queued", "paused"}:
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
                for item in self._items
                if item.status == "queued" and item.id in self._pending
            )

    def running_count(self) -> int:
        with self._lock:
            return len(self._active_ids)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            positions = {request_id: index for index, request_id in enumerate(self._pending, 1)}
            return [
                item.snapshot(position=positions.get(item.id, 0))
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
