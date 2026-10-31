from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlsplit

MAX_BATCH_URLS = 50


def normalize_batch_urls(urls: list[str]) -> tuple[list[str], int]:
    """Validate, preserve order, and deduplicate a batch without fetching URLs."""
    if not urls or len(urls) > MAX_BATCH_URLS:
        raise ValueError(f"Provide 1 to {MAX_BATCH_URLS} URLs per batch.")
    normalized: list[str] = []
    seen: set[str] = set()
    duplicate_count = 0
    for raw in urls:
        url = str(raw or "").strip()
        parsed = urlsplit(url)
        if (
            len(url) > 5000
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(f"Invalid HTTP(S) media URL at item {len(normalized) + duplicate_count + 1}.")
        if url in seen:
            duplicate_count += 1
            continue
        seen.add(url)
        normalized.append(url)
    return normalized, duplicate_count


def batch_summary(batch_id: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    if not jobs:
        raise ValueError("Batch not found.")
    items = sorted(jobs, key=lambda job: int(job.get("request", {}).get("batch_index") or 0))
    counts = Counter(str(job.get("status") or "queued") for job in items)
    total = len(items)
    done = counts["completed"]
    failed = counts["failed"]
    cancelled = counts["cancelled"]
    remaining = total - done - failed - cancelled
    if remaining:
        status = "running" if counts["running"] or counts["cancelling"] else "queued"
    elif failed:
        status = "completed_with_errors"
    elif cancelled and not done:
        status = "cancelled"
    else:
        status = "completed"
    percent = sum(
        1.0 if job.get("status") in {"completed", "failed", "cancelled"}
        else max(0.0, min(1.0, float(job.get("progress") or 0)))
        for job in items
    ) / total
    return {
        "id": batch_id,
        "status": status,
        "total": total,
        "completed": done,
        "failed": failed,
        "cancelled": cancelled,
        "active": remaining,
        "progress": round(percent, 4),
        "created_at": min(float(job.get("created_at") or 0) for job in items),
        "mode": str(items[0].get("request", {}).get("mode") or "Video"),
        "items": items,
    }
