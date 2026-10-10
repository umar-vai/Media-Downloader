from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonStateStore:
    """Small atomic JSON store for Local Core job/history persistence."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return []
            except Exception:
                return []
            items = payload.get("jobs") if isinstance(payload, dict) else None
            return [item for item in (items or []) if isinstance(item, dict)]

    def save(self, jobs: list[dict[str, Any]]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = {"version": 1, "jobs": jobs}
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
