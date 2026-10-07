from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 250


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _normalize_entry(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    path_text = str(payload.get("file_path") or "").strip()
    if not path_text:
        return None

    entry_id = str(payload.get("id") or "").strip() or uuid.uuid4().hex
    created_at = str(payload.get("created_at") or "").strip() or _now_iso()
    title = str(payload.get("title") or "").strip() or Path(path_text).stem or "Media"
    mode = str(payload.get("mode") or "Video").strip().title()
    if mode not in {"Video", "Audio"}:
        mode = "Video"

    return {
        "id": entry_id,
        "created_at": created_at,
        "title": title,
        "file_path": path_text,
        "source_url": str(payload.get("source_url") or "").strip(),
        "platform": str(payload.get("platform") or "").strip().lower(),
        "creator": str(payload.get("creator") or "").strip(),
        "mode": mode,
        "quality": str(payload.get("quality") or "").strip(),
        "duration_seconds": _safe_int(payload.get("duration_seconds")),
        "file_size": _safe_int(payload.get("file_size")),
    }


class HistoryStore:
    def __init__(self, path: Path, limit: int = HISTORY_LIMIT) -> None:
        self.path = Path(path)
        self.limit = max(10, int(limit))
        self._lock = threading.RLock()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

        if isinstance(raw, dict):
            raw = raw.get("items", [])
        if not isinstance(raw, list):
            return []

        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw:
            normalized = _normalize_entry(item)
            if normalized is None or normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            entries.append(normalized)

        entries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return entries[: self.limit]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._read_unlocked()]

    def _write_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "items": entries[: self.limit],
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry = _normalize_entry(payload)
        if entry is None:
            raise ValueError("History entry requires a file_path.")

        with self._lock:
            entries = self._read_unlocked()
            target = os.path.normcase(os.path.abspath(entry["file_path"]))
            entries = [
                item
                for item in entries
                if os.path.normcase(os.path.abspath(str(item["file_path"]))) != target
            ]
            entries.insert(0, entry)
            self._write_unlocked(entries)
        return dict(entry)

    def remove(self, entry_id: str) -> bool:
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return False
        with self._lock:
            entries = self._read_unlocked()
            kept = [item for item in entries if item["id"] != entry_id]
            if len(kept) == len(entries):
                return False
            self._write_unlocked(kept)
            return True

    def clear(self) -> int:
        with self._lock:
            count = len(self._read_unlocked())
            self._write_unlocked([])
            return count

    def prune_missing(self) -> int:
        with self._lock:
            entries = self._read_unlocked()
            kept = [item for item in entries if Path(str(item["file_path"])).exists()]
            removed = len(entries) - len(kept)
            if removed:
                self._write_unlocked(kept)
            return removed

    def most_recent_existing(self) -> dict[str, Any] | None:
        for item in self.list():
            if Path(str(item["file_path"])).exists():
                return item
        return None


def make_history_entry(
    file_path: Path,
    *,
    title: str,
    source_url: str,
    platform: str,
    creator: str = "",
    mode: str = "Video",
    quality: str = "",
    duration_seconds: int = 0,
) -> dict[str, Any]:
    path = Path(file_path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "id": uuid.uuid4().hex,
        "created_at": _now_iso(),
        "title": title,
        "file_path": str(path),
        "source_url": source_url,
        "platform": platform,
        "creator": creator,
        "mode": mode,
        "quality": quality,
        "duration_seconds": duration_seconds,
        "file_size": size,
    }
