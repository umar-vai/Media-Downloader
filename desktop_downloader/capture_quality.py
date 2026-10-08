from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

from browser_capture import sanitize_capture, sanitize_headers
from network_proxy import yt_dlp_proxy_options


def _as_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _quality_label(height: int, fps: float = 0.0) -> str:
    if height <= 0:
        return ""
    suffix = "60" if fps >= 50 else ""
    return f"{height}p{suffix}"


def quality_summary_from_info(info: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(info or {})
    formats = payload.get("formats")
    candidates: list[dict[str, Any]] = []
    if isinstance(formats, list):
        for entry in formats:
            if not isinstance(entry, dict):
                continue
            vcodec = str(entry.get("vcodec") or "").lower()
            height = _as_int(entry.get("height"))
            width = _as_int(entry.get("width"))
            if vcodec == "none" and height <= 0 and width <= 0:
                continue
            candidates.append(entry)

    if not candidates:
        candidates = [payload]

    rows: list[tuple[int, int, float, float]] = []
    heights: set[int] = set()
    for entry in candidates:
        height = _as_int(entry.get("height"))
        width = _as_int(entry.get("width"))
        fps = _as_float(entry.get("fps"))
        tbr = _as_float(entry.get("tbr") or entry.get("vbr"))
        if height > 0:
            heights.add(height)
        if height > 0 or width > 0:
            rows.append((height, width, fps, tbr))

    rows.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    best_height, best_width, best_fps, best_tbr = rows[0] if rows else (0, 0, 0.0, 0.0)

    available = [f"{height}p" for height in sorted(heights, reverse=True)]
    label = _quality_label(best_height, best_fps)
    if len(available) > 1 and label:
        label = f"Best {label}"

    return {
        "quality_status": "ready" if label or available else "unknown",
        "quality_label": label or ("Audio" if str(payload.get("vcodec") or "").lower() == "none" else "Unknown"),
        "height": best_height,
        "width": best_width,
        "fps": best_fps,
        "tbr": best_tbr,
        "available_qualities": available[:12],
        "has_multiple_qualities": len(available) > 1,
    }


def inspect_capture_quality(capture: dict[str, Any]) -> dict[str, Any]:
    item = sanitize_capture(capture)
    kind = str(item.get("kind") or "").lower()
    if kind == "page":
        return {
            "quality_status": "unknown",
            "quality_label": "Auto",
            "height": 0,
            "width": 0,
            "fps": 0.0,
            "tbr": 0.0,
            "available_qualities": [],
            "has_multiple_qualities": False,
        }

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 10,
        "retries": 1,
        "fragment_retries": 1,
        "http_headers": sanitize_headers(item.get("headers")),
        **yt_dlp_proxy_options(),
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(str(item.get("url") or ""), download=False) or {}
        return quality_summary_from_info(info if isinstance(info, dict) else {})
    except Exception:
        return {
            "quality_status": "unknown",
            "quality_label": "Auto",
            "height": 0,
            "width": 0,
            "fps": 0.0,
            "tbr": 0.0,
            "available_qualities": [],
            "has_multiple_qualities": False,
        }


def capture_rank(capture: dict[str, Any]) -> tuple[int, int, int, float]:
    qualities = capture.get("available_qualities")
    multi = 1 if isinstance(qualities, list) and len(qualities) > 1 else 0
    height = _as_int(capture.get("height"))
    width = _as_int(capture.get("width"))
    captured_at = _as_float(capture.get("captured_at"))
    return (height, multi, width, captured_at)


def _host(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def related_capture_candidates(
    captures: list[dict[str, Any]],
    selected: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_item = dict(selected or {})
    group_id = str(selected_item.get("capture_group_id") or "")
    page_url = str(selected_item.get("page_url") or "")
    title = str(selected_item.get("title") or "")
    tab_id = _as_int(selected_item.get("tab_id"))
    selected_kind = str(selected_item.get("kind") or "").lower()
    selected_host = _host(str(selected_item.get("url") or ""))
    selected_at = _as_float(selected_item.get("captured_at"))

    candidates: list[dict[str, Any]] = []
    page_fallbacks: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for raw in captures:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        url = str(item.get("url") or "")
        if not url or url in seen_urls:
            continue

        item_group = str(item.get("capture_group_id") or "")
        kind = str(item.get("kind") or "").lower()

        if group_id:
            same_group = item_group == group_id
        else:
            same_page = bool(page_url and str(item.get("page_url") or "") == page_url)
            same_tab_title = bool(
                tab_id
                and _as_int(item.get("tab_id")) == tab_id
                and title
                and str(item.get("title") or "") == title
            )
            same_stream_family = (
                kind == selected_kind
                and _host(url) == selected_host
            )
            close_in_time = (
                not selected_at
                or not _as_float(item.get("captured_at"))
                or abs(_as_float(item.get("captured_at")) - selected_at) <= 180
            )
            same_group = (same_page or same_tab_title) and same_stream_family and close_in_time

        if same_group and kind in {"hls", "dash", "direct"}:
            candidates.append(item)
            seen_urls.add(url)
            continue

        # Keep a page-level fallback last. It is useful for MSE/blob players
        # and for sites where an individual signed variant expires early.
        same_page_fallback = (
            kind == "page"
            and (
                (group_id and item_group == group_id)
                or (page_url and str(item.get("page_url") or item.get("url") or "") == page_url)
                or (
                    tab_id
                    and _as_int(item.get("tab_id")) == tab_id
                    and title
                    and str(item.get("title") or "") == title
                )
            )
        )
        if same_page_fallback:
            page_fallbacks.append(item)

    if not candidates:
        candidates = [selected_item]

    candidates.sort(key=capture_rank, reverse=True)

    # If the selected item is fresh but quality metadata is still incomplete,
    # make sure it remains a retry candidate instead of being lost.
    selected_url = str(selected_item.get("url") or "")
    if selected_url and all(str(item.get("url") or "") != selected_url for item in candidates):
        candidates.append(selected_item)

    page_fallbacks.sort(
        key=lambda item: _as_float(item.get("captured_at")),
        reverse=True,
    )
    for fallback in page_fallbacks:
        url = str(fallback.get("url") or "")
        if url and all(str(item.get("url") or "") != url for item in candidates):
            candidates.append(fallback)

    return candidates


def select_best_capture(
    captures: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    candidates = related_capture_candidates(captures, selected)
    return dict(candidates[0]) if candidates else dict(selected or {})


__all__ = [
    "capture_rank",
    "inspect_capture_quality",
    "quality_summary_from_info",
    "related_capture_candidates",
    "select_best_capture",
]
