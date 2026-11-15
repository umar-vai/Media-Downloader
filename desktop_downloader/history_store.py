"""Download history (like IDM's list): every finished, failed or cancelled download with all
of its details, so it can be opened, shown in its folder, resumed, downloaded again or
inspected under Properties."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HISTORY_LIMIT = 2000
STATUSES = {"completed", "failed", "cancelled"}

# Free-form details kept for Properties / Redownload. Values are short strings or numbers.
_TEXT_FIELDS = (
    "title", "source_url", "page_url", "download_url", "referer", "platform", "website", "creator",
    "quality", "video_quality", "audio_format", "audio_quality", "error", "download_dir", "file_name", "extension",
    "resolution", "video_codec", "audio_codec", "container", "started_at", "finished_at", "batch_id", "request_id",
)
_INT_FIELDS = ("duration_seconds", "file_size", "elapsed_seconds", "avg_speed_bps", "bitrate_kbps", "attempts")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(timestamp: float) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return default


def website_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _normalize_entry(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status") or "completed").strip().lower()
    if status not in STATUSES:
        status = "completed"
    path_text = str(payload.get("file_path") or "").strip()
    if status == "completed" and not path_text:
        return None
    if not path_text and not str(payload.get("source_url") or "").strip():
        return None

    mode = str(payload.get("mode") or "Video").strip().title()
    if mode not in {"Video", "Audio"}:
        mode = "Video"
    entry: dict[str, Any] = {
        "id": str(payload.get("id") or "").strip() or uuid.uuid4().hex,
        "created_at": str(payload.get("created_at") or "").strip() or _now_iso(),
        "status": status,
        "file_path": path_text,
        "mode": mode,
    }
    for key in _TEXT_FIELDS:
        value = payload.get(key)
        entry[key] = str(value).strip()[:4000] if value is not None else ""
    for key in _INT_FIELDS:
        entry[key] = _safe_int(payload.get(key))
    entry["platform"] = entry["platform"].lower()
    if not entry["title"]:
        entry["title"] = Path(path_text).stem if path_text else "Media"
    if not entry["file_name"] and path_text:
        entry["file_name"] = Path(path_text).name
    if not entry["download_dir"] and path_text:
        entry["download_dir"] = str(Path(path_text).parent)
    if not entry["website"]:
        entry["website"] = website_of(entry["page_url"] or entry["source_url"])
    headers = payload.get("http_headers")
    entry["http_headers"] = (
        {str(k)[:64]: str(v)[:1024] for k, v in list(headers.items())[:8]} if isinstance(headers, dict) else {}
    )
    return entry


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

    def get(self, entry_id: str) -> dict[str, Any] | None:
        for item in self.list():
            if item["id"] == entry_id:
                return item
        return None

    def _write_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
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
            raise ValueError("History entry requires a file_path or a source link.")

        with self._lock:
            entries = self._read_unlocked()
            if entry["file_path"]:
                # The same file downloaded again replaces its older row.
                target = os.path.normcase(os.path.abspath(entry["file_path"]))
                entries = [
                    item
                    for item in entries
                    if not item["file_path"]
                    or os.path.normcase(os.path.abspath(str(item["file_path"]))) != target
                ]
            if entry["status"] == "completed" and entry["source_url"]:
                # A failed/cancelled attempt of the same link is superseded by its success.
                entries = [
                    item
                    for item in entries
                    if not (item["status"] != "completed" and item["source_url"] == entry["source_url"])
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

    def remove_many(self, entry_ids: list[str]) -> int:
        """Remove several entries with one write. Returns how many were removed."""
        wanted = {str(entry_id or "").strip() for entry_id in entry_ids} - {""}
        if not wanted:
            return 0
        with self._lock:
            entries = self._read_unlocked()
            kept = [item for item in entries if item["id"] not in wanted]
            removed = len(entries) - len(kept)
            if removed:
                self._write_unlocked(kept)
            return removed

    def clear(self) -> int:
        with self._lock:
            count = len(self._read_unlocked())
            self._write_unlocked([])
            return count

    def prune_missing(self) -> int:
        with self._lock:
            entries = self._read_unlocked()
            kept = [
                item for item in entries
                if item["status"] != "completed" or Path(str(item["file_path"])).exists()
            ]
            removed = len(entries) - len(kept)
            if removed:
                self._write_unlocked(kept)
            return removed

    def most_recent_existing(self) -> dict[str, Any] | None:
        for item in self.list():
            if item["status"] == "completed" and item["file_path"] and Path(str(item["file_path"])).exists():
                return item
        return None


def make_history_entry(
    file_path: Path | None,
    *,
    title: str,
    source_url: str,
    platform: str,
    creator: str = "",
    mode: str = "Video",
    quality: str = "",
    duration_seconds: int = 0,
    status: str = "completed",
    started_at: float = 0.0,
    finished_at: float = 0.0,
    **details: Any,
) -> dict[str, Any]:
    """Build a history row. Extra keyword ``details`` (page_url, referer, error, resolution,
    ...) are stored as-is for the Properties dialog."""
    path = Path(file_path) if file_path else None
    size = 0
    if path is not None:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
    elapsed = max(0.0, float(finished_at or 0) - float(started_at or 0)) if started_at and finished_at else 0.0
    entry = {
        "id": uuid.uuid4().hex,
        "created_at": _iso(finished_at) or _now_iso(),
        "status": status,
        "title": title,
        "file_path": str(path) if path is not None else "",
        "source_url": source_url,
        "platform": platform,
        "creator": creator,
        "mode": mode,
        "quality": quality,
        "duration_seconds": duration_seconds,
        "file_size": size,
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "elapsed_seconds": int(round(elapsed)),
        "avg_speed_bps": int(size / elapsed) if size and elapsed >= 1 else 0,
        "extension": path.suffix.lstrip(".").upper() if path is not None else "",
    }
    for key, value in details.items():
        if value not in (None, ""):
            entry[key] = value
    return entry
