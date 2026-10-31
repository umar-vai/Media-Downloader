from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

MAX_BATCH_URLS = 50
ACTIVE = frozenset({"queued", "running", "cancelling"})


def parse_batch_urls(text: str) -> tuple[list[str], int]:
    """Validate 1–50 HTTP(S) URLs; preserve order and skip duplicates."""
    tokens = [token.strip() for token in re.split(r"[\s]+", text or "") if token.strip()]
    if not tokens or len(tokens) > MAX_BATCH_URLS:
        raise ValueError(f"Paste between 1 and {MAX_BATCH_URLS} URLs, one per line.")
    urls: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for index, token in enumerate(tokens, 1):
        parsed = urlsplit(token)
        if (
            len(token) > 5000
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(f"Invalid HTTP(S) URL on item {index}.")
        if token in seen:
            duplicates += 1
            continue
        seen.add(token)
        urls.append(token)
    return urls, duplicates


def batch_summaries(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in snapshots:
        batch_id = str(item.get("batch_id") or "")
        if batch_id:
            groups.setdefault(batch_id, []).append(item)

    summaries: list[dict[str, Any]] = []
    for batch_id, jobs in groups.items():
        jobs.sort(key=lambda item: int(item.get("batch_index") or 0))
        counts = Counter(str(item.get("status") or "queued") for item in jobs)
        active = sum(counts[status] for status in ACTIVE)
        progress = sum(
            1.0 if job.get("status") in {"completed", "failed", "cancelled"}
            else max(0.0, min(1.0, float(job.get("progress") or 0.0)))
            for job in jobs
        ) / len(jobs)
        summaries.append({
            "batch_id": batch_id,
            "items": jobs,
            "total": len(jobs),
            "active": active,
            "completed": counts["completed"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
            "progress": progress,
            "created_at": min(float(job.get("created_at") or 0) for job in jobs),
        })
    summaries.sort(key=lambda item: item["created_at"], reverse=True)
    return summaries


def batch_filename(batch_id: str, batch_index: int, title: str) -> str:
    """Stable unique prefix so identical media titles do not collide."""
    from media_core.engine import safe_filename
    stem = safe_filename(title or "media")
    return f"{batch_id[:8]}_{batch_index:03d}_{stem[:100]}"
