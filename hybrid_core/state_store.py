from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any


class JsonStateStore:
    """Atomic JSON store with backup recovery for Local Core job/history state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        self._lock = threading.RLock()

    @staticmethod
    def _jobs_from(path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("jobs") if isinstance(payload, dict) else None
        return [item for item in (items or []) if isinstance(item, dict)]

    def _quarantine_corrupt(self) -> None:
        if not self.path.exists():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantine = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}{self.path.suffix}")
        try:
            self.path.replace(quarantine)
        except OSError:
            pass

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                return self._jobs_from(self.path)
            except FileNotFoundError:
                pass
            except Exception:
                self._quarantine_corrupt()

            try:
                recovered = self._jobs_from(self.backup_path)
            except Exception:
                return []

            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.backup_path, self.path)
            except OSError:
                pass
            return recovered

    def save(self, jobs: list[dict[str, Any]]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = {"version": 2, "jobs": jobs}
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            if self.path.exists():
                try:
                    self._jobs_from(self.path)
                    shutil.copy2(self.path, self.backup_path)
                except Exception:
                    self._quarantine_corrupt()

            temp.replace(self.path)
