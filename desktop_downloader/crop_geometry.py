"""Geometry for the on-video crop box: move, resize from any handle, ratio lock.

All values are in source-video pixels. A rect is ``(x, y, width, height)`` as floats while
dragging; :func:`finalize` turns it into the even integer box FFmpeg needs.
"""
from __future__ import annotations

Rect = tuple[float, float, float, float]

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
MIN_SIZE = 16.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def full_frame(width: int, height: int) -> Rect:
    return 0.0, 0.0, float(width), float(height)


def fit_ratio(rect: Rect, ratio: float | None, frame_w: int, frame_h: int) -> Rect:
    """The largest box of ``ratio`` inside ``rect``, sharing its centre (keeps ``rect`` if no ratio)."""
    x, y, w, h = rect
    if not ratio:
        return keep_inside((x, y, w, h), frame_w, frame_h)
    cx, cy = x + w / 2, y + h / 2
    if w / max(h, 1e-6) > ratio:
        w = h * ratio
    else:
        h = w / ratio
    return keep_inside((cx - w / 2, cy - h / 2, w, h), frame_w, frame_h)


def centered(ratio: float | None, frame_w: int, frame_h: int, scale: float = 1.0) -> Rect:
    """The largest box of ``ratio`` that fits the frame, centred, optionally scaled down."""
    box = fit_ratio(full_frame(frame_w, frame_h), ratio, frame_w, frame_h)
    _x, _y, w, h = box
    w, h = w * scale, h * scale
    return (frame_w - w) / 2, (frame_h - h) / 2, w, h


def keep_inside(rect: Rect, frame_w: int, frame_h: int) -> Rect:
    """Shrink then slide the box so it lies within the frame."""
    x, y, w, h = rect
    w = clamp(w, 1.0, float(frame_w))
    h = clamp(h, 1.0, float(frame_h))
    x = clamp(x, 0.0, frame_w - w)
    y = clamp(y, 0.0, frame_h - h)
    return x, y, w, h


def move(rect: Rect, dx: float, dy: float, frame_w: int, frame_h: int) -> Rect:
    x, y, w, h = rect
    return keep_inside((x + dx, y + dy, w, h), frame_w, frame_h)


def resize(
    rect: Rect,
    handle: str,
    dx: float,
    dy: float,
    frame_w: int,
    frame_h: int,
    ratio: float | None = None,
    min_size: float = MIN_SIZE,
) -> Rect:
    """Drag ``handle`` of ``rect`` by (dx, dy). The opposite side or corner stays fixed."""
    if handle == "move":
        return move(rect, dx, dy, frame_w, frame_h)
    x, y, w, h = rect
    left, top, right, bottom = x, y, x + w, y + h
    min_w = min(min_size, float(frame_w))
    min_h = min(min_size, float(frame_h))

    if ratio and handle in {"nw", "ne", "sw", "se"}:
        # Opposite corner is the anchor; grow by whichever direction moved further.
        ax = right if "w" in handle else left
        ay = bottom if "n" in handle else top
        sign_x = -1 if "w" in handle else 1
        sign_y = -1 if "n" in handle else 1
        new_w = w + sign_x * dx
        new_h = h + sign_y * dy
        new_w = max(new_w, new_h * ratio)
        room_w = ax if sign_x < 0 else frame_w - ax
        room_h = ay if sign_y < 0 else frame_h - ay
        new_w = min(new_w, room_w, room_h * ratio)
        new_w = max(new_w, min_w, min_h * ratio)
        new_w = min(new_w, room_w, room_h * ratio)
        new_h = new_w / ratio
        nx = ax - new_w if sign_x < 0 else ax
        ny = ay - new_h if sign_y < 0 else ay
        return nx, ny, new_w, new_h

    if ratio and handle in {"e", "w"}:
        cy = y + h / 2
        ax = right if handle == "w" else left
        new_w = w + (dx if handle == "e" else -dx)
        room_w = ax if handle == "w" else frame_w - ax
        # Height may be capped by the centre; slide the box vertically when it would not fit.
        max_w = min(room_w, frame_h * ratio)
        new_w = clamp(new_w, max(min_w, min_h * ratio), max_w)
        new_h = new_w / ratio
        ny = clamp(cy - new_h / 2, 0.0, frame_h - new_h)
        nx = ax - new_w if handle == "w" else ax
        return nx, ny, new_w, new_h

    if ratio and handle in {"n", "s"}:
        cx = x + w / 2
        ay = bottom if handle == "n" else top
        new_h = h + (dy if handle == "s" else -dy)
        room_h = ay if handle == "n" else frame_h - ay
        max_h = min(room_h, frame_w / ratio)
        new_h = clamp(new_h, max(min_h, min_w / ratio), max_h)
        new_w = new_h * ratio
        nx = clamp(cx - new_w / 2, 0.0, frame_w - new_w)
        ny = ay - new_h if handle == "n" else ay
        return nx, ny, new_w, new_h

    # Free resize: each dragged edge moves on its own.
    if "w" in handle:
        left = clamp(left + dx, 0.0, right - min_w)
    if "e" in handle:
        right = clamp(right + dx, left + min_w, float(frame_w))
    if "n" in handle:
        top = clamp(top + dy, 0.0, bottom - min_h)
    if "s" in handle:
        bottom = clamp(bottom + dy, top + min_h, float(frame_h))
    return left, top, right - left, bottom - top


def from_points(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    frame_w: int,
    frame_h: int,
    ratio: float | None = None,
) -> Rect:
    """A new box drawn by dragging from (ax, ay) to (bx, by)."""
    ax, ay = clamp(ax, 0.0, frame_w), clamp(ay, 0.0, frame_h)
    bx, by = clamp(bx, 0.0, frame_w), clamp(by, 0.0, frame_h)
    w, h = abs(bx - ax), abs(by - ay)
    if ratio:
        w = max(w, h * ratio)
        room_w = ax if bx < ax else frame_w - ax
        room_h = ay if by < ay else frame_h - ay
        w = min(w, room_w, room_h * ratio)
        h = w / ratio
    x = ax - w if bx < ax else ax
    y = ay - h if by < ay else ay
    return x, y, w, h


def nudge(rect: Rect, dx: float, dy: float, frame_w: int, frame_h: int) -> Rect:
    return move(rect, dx, dy, frame_w, frame_h)


def finalize(rect: Rect, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Even integer box inside the frame, as FFmpeg's crop filter wants it."""
    x, y, w, h = keep_inside(rect, frame_w, frame_h)
    width = max(2, int(round(w)) // 2 * 2)
    height = max(2, int(round(h)) // 2 * 2)
    width = min(width, frame_w // 2 * 2)
    height = min(height, frame_h // 2 * 2)
    left = int(round(x))
    top = int(round(y))
    left = max(0, min(left, frame_w - width))
    top = max(0, min(top, frame_h - height))
    return left, top, width, height


def hit_test(
    rect: Rect,
    px: float,
    py: float,
    tolerance: float,
) -> str | None:
    """Which handle (or "move") is under a point, in the same units as ``rect``."""
    x, y, w, h = rect
    left, top, right, bottom = x, y, x + w, y + h
    near_l = abs(px - left) <= tolerance
    near_r = abs(px - right) <= tolerance
    near_t = abs(py - top) <= tolerance
    near_b = abs(py - bottom) <= tolerance
    inside_x = left - tolerance <= px <= right + tolerance
    inside_y = top - tolerance <= py <= bottom + tolerance
    if not (inside_x and inside_y):
        return None
    # On a small box both edges can be in reach: take the nearer one.
    vertical = ""
    if near_t or near_b:
        vertical = "n" if abs(py - top) <= abs(py - bottom) else "s"
    horizontal = ""
    if near_l or near_r:
        horizontal = "w" if abs(px - left) <= abs(px - right) else "e"
    if (vertical or horizontal) and w <= 3 * tolerance and h <= 3 * tolerance:
        return "move"
    if vertical or horizontal:
        return vertical + horizontal
    if left < px < right and top < py < bottom:
        return "move"
    return None
