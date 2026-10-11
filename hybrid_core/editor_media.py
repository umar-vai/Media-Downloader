from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaEntry:
    path: Path
    expires_at: float
    touched_at: float


class EditorMediaRegistry:
    """Opaque, short-lived references to local editor media for browser playback."""

    def __init__(self, *, ttl_seconds: float = 6 * 60 * 60, max_entries: int = 64) -> None:
        self.ttl_seconds = max(300.0, float(ttl_seconds))
        self.max_entries = max(8, int(max_entries))
        self._items: dict[str, MediaEntry] = {}
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        expired = [token for token, item in self._items.items() if item.expires_at <= now or not item.path.is_file()]
        for token in expired:
            self._items.pop(token, None)

        if len(self._items) > self.max_entries:
            oldest = sorted(self._items.items(), key=lambda pair: pair[1].touched_at)
            for token, _item in oldest[: len(self._items) - self.max_entries]:
                self._items.pop(token, None)

    def register(self, path: Path) -> str:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            for token, item in self._items.items():
                if item.path == source:
                    item.expires_at = now + self.ttl_seconds
                    item.touched_at = now
                    return token

            token = secrets.token_urlsafe(32)
            self._items[token] = MediaEntry(
                path=source,
                expires_at=now + self.ttl_seconds,
                touched_at=now,
            )
            self._prune_locked(now)
            return token

    def resolve(self, token: str) -> Path | None:
        if not token or len(token) > 256:
            return None
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            item = self._items.get(token)
            if item is None:
                return None
            if not item.path.is_file():
                self._items.pop(token, None)
                return None
            item.touched_at = now
            item.expires_at = now + self.ttl_seconds
            return item.path
