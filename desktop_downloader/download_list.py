"""The data behind the main download list: one row per download, IDM style.

Live downloads come from ``DownloadQueue.snapshot()`` and finished ones from the history
store. Both are turned into plain row dicts here, so filtering by category, searching,
sorting and the toolbar's enabled actions can be tested without a window.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

# ------------------------------------------------------------------ categories
FILE_CATEGORIES: list[tuple[str, str]] = [
    ("video", "Video"),
    ("audio", "Audio"),
    ("compressed", "Compressed"),
    ("documents", "Documents"),
    ("programs", "Programs"),
    ("misc", "Misc"),
]
CATEGORY_LABELS = dict(FILE_CATEGORIES)

_EXTENSIONS: dict[str, set[str]] = {
    "video": {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "ts", "m2ts", "3gp", "mpg", "mpeg", "ogv"},
    "audio": {"mp3", "m4a", "aac", "flac", "opus", "ogg", "wav", "wma", "alac", "aiff", "aif", "mka"},
    "compressed": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "cab"},
    "documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "odt", "ods", "odp", "csv",
                  "epub", "md"},
    "programs": {"exe", "msi", "msix", "apk", "dmg", "pkg", "deb", "rpm", "appimage", "bat", "cmd"},
}
_EXT_TO_CATEGORY = {ext: cat for cat, exts in _EXTENSIONS.items() for ext in exts}


def extension_of(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower().lstrip(".")
    return suffix if suffix.isalnum() and len(suffix) <= 9 else ""


def category_for(name: str, mode: str = "") -> str:
    """File category from the extension; a download without one yet uses its Video/Audio mode."""
    category = _EXT_TO_CATEGORY.get(extension_of(name))
    if category:
        return category
    mode = str(mode or "").strip().lower()
    if mode == "audio":
        return "audio"
    if mode == "video":
        return "video"
    return "misc"


# ------------------------------------------------------------------ statuses
UNFINISHED = {"downloading", "connecting", "queued", "retrying", "paused", "stopping", "failed", "cancelled"}
FINISHED = {"complete", "missing"}
_STATUS_RANK = {
    "downloading": 0, "connecting": 1, "stopping": 2, "retrying": 3, "queued": 4, "paused": 5,
    "failed": 6, "cancelled": 7, "missing": 8, "complete": 9,
}
RESUMABLE = {"paused", "failed", "cancelled"}
STOPPABLE = {"downloading", "connecting", "queued", "retrying"}
_QUEUE_TERMINAL = {"completed", "failed", "cancelled"}


# ------------------------------------------------------------------ formatting
def format_size(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return ""


def format_speed(bps: Any) -> str:
    text = format_size(bps)
    return f"{text}/s" if text else ""


def format_eta(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    if total < 60:
        return f"{total} sec"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} sec"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} hr {minutes:02d} min"
    return f"{hours // 24} days"


def format_date(stamp: float) -> str:
    if not stamp:
        return ""
    try:
        return datetime.fromtimestamp(float(stamp)).strftime("%b %d %Y  %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def parse_iso(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def website_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


# ------------------------------------------------------------------ rows
def queue_row(item: dict[str, Any], metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """A row for a download in the live queue (``DownloadQueue.snapshot()`` item)."""
    metrics = metrics or {}
    status = str(item.get("status") or "queued")
    progress = 1.0 if status == "completed" else max(0.0, min(1.0, float(item.get("progress") or 0.0)))
    detail = str(item.get("detail") or "")
    path = str(item.get("result_path") or "")
    name = Path(path).name if path else str(item.get("name") or "Media download")
    attempts = int(item.get("auto_attempts") or 0)

    if status == "running":
        state = "downloading" if progress > 0.0005 else "connecting"
        text = f"{progress * 100:.1f}%" if state == "downloading" else "Connecting…"
    elif status == "queued":
        state = "retrying" if attempts else "queued"
        text = f"Retrying (#{attempts})" if attempts else "Queued"
    elif status == "paused":
        state = "paused"
        text = f"Paused  {progress * 100:.0f}%" if progress > 0.0005 else "Paused"
    elif status == "cancelling":
        state, text = "stopping", "Stopping…"
    elif status == "completed":
        state, text = "complete", "Complete"
    elif status == "failed":
        state, text = "failed", "Failed"
    else:
        state, text = "cancelled", "Cancelled"

    total = int(metrics.get("total_bytes") or 0)
    done = int(metrics.get("downloaded_bytes") or 0)
    size = total or done
    if status == "completed" and path:
        try:
            size = os.path.getsize(path)
        except OSError:
            pass
    live = state in {"downloading", "connecting"}
    speed = float(metrics.get("speed_bps") or 0.0) if live else 0.0
    eta = metrics.get("eta_seconds") if live else None
    url = str(item.get("url") or "")
    page_url = str(item.get("page_url") or "") or url
    save_to = path or str(item.get("download_dir") or "")
    created = float(item.get("created_at") or 0)
    last_try = float(item.get("finished_at") or 0) or float(item.get("started_at") or 0) or created
    return {
        "key": f"q:{item.get('id')}",
        "source": "queue",
        "request_id": str(item.get("id") or ""),
        "history_id": "",
        "name": name,
        "mode": str(item.get("mode") or ""),
        "category": category_for(name, str(item.get("mode") or "")),
        "status": state,
        "status_text": text,
        "detail": str(item.get("error") or detail) if status == "failed" else detail,
        "progress": progress,
        "size": size,
        "size_text": format_size(size),
        "done_bytes": done,
        "speed": speed,
        "speed_text": format_speed(speed),
        "eta": eta,
        "eta_text": format_eta(eta) if eta is not None else "",
        "added": created,
        "added_text": format_date(created),
        "last_try": last_try,
        "last_try_text": format_date(last_try),
        "save_to": save_to,
        "path": path,
        "url": url,
        "page_url": page_url,
        "website": website_of(page_url),
        "quality": str(item.get("quality") or ""),
        "batch_id": str(item.get("batch_id") or ""),
        "error": str(item.get("last_error") or item.get("error") or ""),
        "has_details": status == "failed" or (status == "queued" and attempts > 0),
        "file_exists": bool(path) and status == "completed",
    }


def history_row(entry: dict[str, Any], *, exists: bool | None = None) -> dict[str, Any]:
    """A row for a download kept in the history store (finished, failed or cancelled)."""
    status = str(entry.get("status") or "completed")
    path = str(entry.get("file_path") or "")
    if exists is None:
        exists = bool(path) and Path(path).exists()
    if status == "completed":
        state, text = ("complete", "Complete") if exists else ("missing", "File missing")
    elif status == "failed":
        state, text = "failed", "Failed"
    else:
        state, text = "cancelled", "Cancelled"
    name = str(entry.get("file_name") or "") or (Path(path).name if path else "") or str(entry.get("title") or "Media")
    size = int(entry.get("file_size") or 0)
    finished = parse_iso(entry.get("finished_at")) or parse_iso(entry.get("created_at"))
    added = parse_iso(entry.get("started_at")) or finished
    page_url = str(entry.get("page_url") or entry.get("source_url") or "")
    url = str(entry.get("download_url") or entry.get("source_url") or "")
    return {
        "key": f"h:{entry.get('id')}",
        "source": "history",
        "request_id": str(entry.get("request_id") or ""),
        "history_id": str(entry.get("id") or ""),
        "name": name,
        "mode": str(entry.get("mode") or ""),
        "category": category_for(name, str(entry.get("mode") or "")),
        "status": state,
        "status_text": text,
        "detail": str(entry.get("error") or ""),
        "progress": 1.0 if status == "completed" else 0.0,
        "size": size,
        "size_text": format_size(size),
        "done_bytes": size,
        "speed": 0.0,
        "speed_text": format_speed(entry.get("avg_speed_bps")) if state == "complete" else "",
        "eta": None,
        "eta_text": "",
        "added": added,
        "added_text": format_date(added),
        "last_try": finished,
        "last_try_text": format_date(finished),
        "save_to": path or str(entry.get("download_dir") or ""),
        "path": path,
        "url": url,
        "page_url": page_url,
        "website": str(entry.get("website") or "") or website_of(page_url),
        "quality": str(entry.get("resolution") or entry.get("quality") or ""),
        "batch_id": str(entry.get("batch_id") or ""),
        "error": str(entry.get("error") or ""),
        "has_details": False,
        "file_exists": bool(exists) and status == "completed",
    }


def merge_rows(queue_rows: Iterable[dict[str, Any]], history_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per download. A live (unfinished) queue item wins over its history entry; a
    finished queue item is represented by its history entry, which has the file details."""
    queue_rows = list(queue_rows)
    history_rows = list(history_rows)
    recorded = {row["request_id"] for row in history_rows if row["request_id"]}
    live = {row["request_id"] for row in queue_rows if row["status"] not in {"complete", "failed", "cancelled"}}
    rows = [row for row in queue_rows if row["request_id"] in live or row["request_id"] not in recorded]
    rows.extend(row for row in history_rows if not row["request_id"] or row["request_id"] not in live)
    return rows


# ------------------------------------------------------------------ filtering
def in_category(row: dict[str, Any], category: str) -> bool:
    if category in ("", "all"):
        return True
    if category == "unfinished":
        return row["status"] in UNFINISHED
    if category == "finished":
        return row["status"] in FINISHED
    if category == "queue:active":
        return row["source"] == "queue" and row["status"] in STOPPABLE | {"paused", "stopping"}
    if category.startswith("queue:"):
        return row["source"] == "queue" and row["batch_id"] == category[6:]
    return row["category"] == category


def matches_query(row: dict[str, Any], query: str) -> bool:
    words = [word for word in str(query or "").lower().split() if word]
    if not words:
        return True
    haystack = " ".join(
        str(row.get(key) or "") for key in ("name", "status_text", "website", "url", "page_url", "save_to", "quality")
    ).lower()
    return all(word in haystack for word in words)


def filter_rows(rows: Iterable[dict[str, Any]], category: str = "all", query: str = "") -> list[dict[str, Any]]:
    return [row for row in rows if in_category(row, category) and matches_query(row, query)]


def category_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key, _label in FILE_CATEGORIES}
    counts.update({"all": 0, "unfinished": 0, "finished": 0, "queue:active": 0})
    for row in rows:
        counts["all"] += 1
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        if row["status"] in UNFINISHED:
            counts["unfinished"] += 1
        if row["status"] in FINISHED:
            counts["finished"] += 1
        if in_category(row, "queue:active"):
            counts["queue:active"] += 1
    return counts


def queue_groups(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batches of two or more links that are in this session's queue, oldest first."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["source"] == "queue" and row["batch_id"]:
            groups.setdefault(row["batch_id"], []).append(row)
    ordered = sorted(
        ((batch_id, items) for batch_id, items in groups.items() if len(items) > 1),
        key=lambda pair: min(item["added"] for item in pair[1]),
    )
    result = []
    for number, (batch_id, items) in enumerate(ordered, 1):
        done = sum(1 for item in items if item["status"] == "complete")
        result.append({"key": f"queue:{batch_id}", "batch_id": batch_id, "label": f"Batch {number}",
                       "done": done, "total": len(items),
                       "active": sum(1 for item in items if item["status"] in STOPPABLE | {"stopping"}),
                       "failed": sum(1 for item in items if item["status"] == "failed")})
    return result


# ------------------------------------------------------------------ sorting
COLUMNS: list[tuple[str, str, int, str]] = [
    # key, heading, default width (design px), anchor
    ("name", "File Name", 290, "w"),
    ("size", "Size", 82, "e"),
    ("status", "Status", 100, "w"),
    ("speed", "Transfer rate", 96, "e"),
    ("eta", "Time left", 96, "e"),
    ("last_try", "Last Try Date", 134, "w"),
    ("added", "Date Added", 134, "w"),
    ("website", "Website", 120, "w"),
    ("quality", "Quality", 80, "w"),
    ("save_to", "Save To", 250, "w"),
]
COLUMN_KEYS = [key for key, *_rest in COLUMNS]
DEFAULT_VISIBLE = ["size", "status", "speed", "eta", "last_try", "save_to"]


def normalize_columns(value: Any) -> list[str]:
    """Visible data columns in display order (the name column is always shown)."""
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_VISIBLE)
    seen: list[str] = []
    for key in value:
        key = str(key)
        if key in COLUMN_KEYS and key != "name" and key not in seen:
            seen.append(key)
    return seen or list(DEFAULT_VISIBLE)


def sort_key(row: dict[str, Any], column: str) -> Any:
    if column == "size":
        return row["size"]
    if column == "status":
        return (_STATUS_RANK.get(row["status"], 50), -row["progress"])
    if column == "speed":
        return row["speed"]
    if column == "eta":
        return row["eta"] if row["eta"] is not None else float("inf")
    if column == "last_try":
        return row["last_try"]
    if column == "added":
        return row["added"]
    return str(row.get(column) or "").lower()


def sort_rows(rows: list[dict[str, Any]], column: str = "added", descending: bool = True) -> list[dict[str, Any]]:
    column = column if column in COLUMN_KEYS else "added"
    # Ties keep the newest download first so the list never jumps around.
    ordered = sorted(rows, key=lambda row: row["added"], reverse=True)
    return sorted(ordered, key=lambda row: sort_key(row, column), reverse=descending)


# ------------------------------------------------------------------ actions
def available_actions(rows: list[dict[str, Any]]) -> set[str]:
    """Which toolbar / menu actions make sense for the selected rows."""
    actions: set[str] = set()
    if not rows:
        return actions
    actions.update({"delete", "copy_link"} if any(row["url"] for row in rows) else {"delete"})
    for row in rows:
        if row["status"] in RESUMABLE:
            actions.add("resume")
        if row["status"] in STOPPABLE:
            actions.add("stop")
        if row["source"] == "queue" and row["status"] in STOPPABLE | {"paused"}:
            actions.add("cancel")
        if row["status"] in FINISHED | {"failed", "cancelled"} and row["url"]:
            actions.add("redownload")
    single = rows[0] if len(rows) == 1 else None
    if single is not None:
        actions.add("properties")
        if single["save_to"]:
            actions.add("open_folder")
        if single["status"] == "complete" and single["file_exists"]:
            actions.update({"open", "edit"})
        if single["has_details"]:
            actions.add("details")
        if single["page_url"].startswith(("http://", "https://")):
            actions.add("open_page")
    return actions


def selection_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    if len(rows) == 1:
        return rows[0]["name"]
    total = sum(row["size"] for row in rows)
    size = format_size(total)
    return f"{len(rows)} items selected" + (f" • {size}" if size else "")


def totals(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    downloading = queued = paused = failed = 0
    speed = 0.0
    for row in rows:
        status = row["status"]
        if status in {"downloading", "connecting", "stopping"}:
            downloading += 1
            speed += row["speed"]
        elif status in {"queued", "retrying"}:
            queued += 1
        elif status == "paused":
            paused += 1
        elif status == "failed" and row["source"] == "queue":
            failed += 1
    return {"downloading": downloading, "queued": queued, "paused": paused, "failed": failed, "speed": speed,
            "speed_text": format_speed(speed)}

