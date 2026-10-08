from __future__ import annotations

from typing import Any

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


def select_best_capture(
    captures: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    selected_item = dict(selected or {})
    page_url = str(selected_item.get("page_url") or "")
    title = str(selected_item.get("title") or "")
    tab_id = _as_int(selected_item.get("tab_id"))

    candidates: list[dict[str, Any]] = []
    for item in captures:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        if kind not in {"hls", "dash", "direct"}:
            continue
        same_page = bool(page_url and str(item.get("page_url") or "") == page_url)
        same_tab_title = bool(
            tab_id
            and _as_int(item.get("tab_id")) == tab_id
            and title
            and str(item.get("title") or "") == title
        )
        if same_page or same_tab_title:
            candidates.append(dict(item))

    if not candidates:
        return selected_item

    best = max(candidates, key=capture_rank)
    if capture_rank(best) > capture_rank(selected_item):
        return best
    return selected_item


__all__ = [
    "capture_rank",
    "inspect_capture_quality",
    "quality_summary_from_info",
    "select_best_capture",
]
