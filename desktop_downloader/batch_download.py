from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

MAX_BATCH_URLS = 300
ACTIVE = frozenset({"queued", "running", "cancelling"})


_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>’”"


def parse_batch_urls_detailed(text: str) -> tuple[list[str], int, int]:
    """Extract http(s) links from pasted text.

    Returns ``(urls, duplicates_skipped, invalid_skipped)``. Bad lines are skipped
    instead of rejecting the whole batch, and links are pulled out of messy text
    (numbered lists, quotes, trailing commas, several links on a line).
    """
    urls: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    invalid = 0
    for match in _URL_RE.finditer(text or ""):
        token = match.group(0).rstrip(_TRAILING)
        try:
            parsed = urlsplit(token)
            hostname = parsed.hostname
            bad = (
                len(token) > 5000
                or parsed.scheme.lower() not in {"http", "https"}
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
            )
        except ValueError:
            bad = True
        if bad:
            invalid += 1
            continue
        key = token
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        urls.append(token)
    # non-URL junk (e.g. "ftp://x", "file:///a") counts as invalid so the user is told
    leftovers = _URL_RE.sub(" ", text or "")
    invalid += sum(1 for part in leftovers.split() if "://" in part)
    if not urls:
        raise ValueError(f"Paste between 1 and {MAX_BATCH_URLS} valid http(s) links, one per line.")
    if len(urls) > MAX_BATCH_URLS:
        raise ValueError(
            f"A batch can hold up to {MAX_BATCH_URLS} links; you pasted {len(urls)}. "
            "Split it into two batches."
        )
    return urls, duplicates, invalid


def parse_batch_urls(text: str) -> tuple[list[str], int]:
    """Validate 1–300 HTTP(S) URLs; preserve order and skip duplicates."""
    urls, duplicates, _invalid = parse_batch_urls_detailed(text)
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
    """Plain media title. Duplicate titles are made unique by the caller / the engine ("Title (2)")."""
    from media_core.engine import safe_filename
    return safe_filename(title or "media")[:120]
