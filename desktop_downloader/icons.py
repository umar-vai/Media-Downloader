"""The app's own icon set, drawn with Pillow at the screen's real pixel size.

Nothing here is copied from another product: every glyph is a few simple shapes drawn at
4x and scaled down, so icons stay crisp at 100%, 150% or 200% display scaling.
"""

from __future__ import annotations

import math
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

SS = 4  # supersampling factor

BLUE = "#2563EB"
SKY = "#0EA5E9"
GREEN = "#16A34A"
ORANGE = "#EA580C"
AMBER = "#CA8A04"
RED = "#DC2626"
VIOLET = "#7C3AED"
TEAL = "#0D9488"
SLATE = "#64748B"
WHITE = "#FFFFFF"

CATEGORY_COLORS = {
    "video": VIOLET, "audio": ORANGE, "compressed": AMBER, "documents": BLUE, "programs": TEAL, "misc": SLATE,
}

Painter = Callable[[ImageDraw.ImageDraw, float], None]


def _box(u: float, x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [x0 * u, y0 * u, x1 * u, y1 * u]


def _circle(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, r: float, fill: str, outline: str | None = None,
            width: float = 0) -> None:
    d.ellipse(_box(u, cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline,
              width=max(1, int(width * u)) if outline else 0)


def _poly(d: ImageDraw.ImageDraw, u: float, points: list[tuple[float, float]], fill: str) -> None:
    d.polygon([(x * u, y * u) for x, y in points], fill=fill)


def _line(d: ImageDraw.ImageDraw, u: float, points: list[tuple[float, float]], fill: str, width: float) -> None:
    d.line([(x * u, y * u) for x, y in points], fill=fill, width=max(1, int(width * u)), joint="curve")


def _rrect(d: ImageDraw.ImageDraw, u: float, x0: float, y0: float, x1: float, y1: float, r: float, fill: str | None,
           outline: str | None = None, width: float = 0) -> None:
    d.rounded_rectangle(_box(u, x0, y0, x1, y1), radius=r * u, fill=fill, outline=outline,
                        width=max(1, int(width * u)) if outline else 0)


def _render(size: int, paint: Painter) -> Image.Image:
    big = size * SS
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    paint(ImageDraw.Draw(image), float(big))
    return image.resize((size, size), Image.Resampling.LANCZOS)


# ------------------------------------------------------------------ glyph parts
def _play(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, s: float, fill: str) -> None:
    _poly(d, u, [(cx - s * 0.42, cy - s * 0.55), (cx - s * 0.42, cy + s * 0.55), (cx + s * 0.58, cy)], fill)


def _pause(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, s: float, fill: str) -> None:
    w, h = s * 0.2, s * 0.5
    _rrect(d, u, cx - s * 0.3, cy - h, cx - s * 0.3 + w, cy + h, w * 0.3, fill)
    _rrect(d, u, cx + s * 0.1, cy - h, cx + s * 0.1 + w, cy + h, w * 0.3, fill)


def _check(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, s: float, fill: str, width: float) -> None:
    _line(d, u, [(cx - s * 0.45, cy), (cx - s * 0.12, cy + s * 0.32), (cx + s * 0.48, cy - s * 0.34)], fill, width)


def _cross(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, s: float, fill: str, width: float) -> None:
    _line(d, u, [(cx - s * 0.36, cy - s * 0.36), (cx + s * 0.36, cy + s * 0.36)], fill, width)
    _line(d, u, [(cx - s * 0.36, cy + s * 0.36), (cx + s * 0.36, cy - s * 0.36)], fill, width)


def _arrow_down(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, s: float, fill: str, width: float) -> None:
    _line(d, u, [(cx, cy - s * 0.48), (cx, cy + s * 0.3)], fill, width)
    _line(d, u, [(cx - s * 0.36, cy - s * 0.02), (cx, cy + s * 0.4), (cx + s * 0.36, cy - s * 0.02)], fill, width)


def _trash(d: ImageDraw.ImageDraw, u: float, x: float, y: float, s: float, fill: str) -> None:
    _rrect(d, u, x + s * 0.30, y + s * 0.02, x + s * 0.70, y + s * 0.16, s * 0.05, None, fill, s * 0.07)
    _rrect(d, u, x + s * 0.06, y + s * 0.13, x + s * 0.94, y + s * 0.25, s * 0.05, fill)
    _poly(d, u, [(x + s * 0.14, y + s * 0.30), (x + s * 0.86, y + s * 0.30), (x + s * 0.79, y + s * 1.0),
                 (x + s * 0.21, y + s * 1.0)], fill)
    for fx in (0.36, 0.5, 0.64):
        _line(d, u, [(x + s * fx, y + s * 0.42), (x + s * fx, y + s * 0.86)], WHITE, s * 0.06)


def _lines(d: ImageDraw.ImageDraw, u: float, x: float, y: float, w: float, gap: float, count: int, fill: str,
           width: float) -> None:
    for i in range(count):
        _line(d, u, [(x, y + i * gap), (x + w, y + i * gap)], fill, width)


def _badge(d: ImageDraw.ImageDraw, u: float, cx: float, cy: float, r: float, fill: str, ring: str) -> None:
    _circle(d, u, cx, cy, r + 0.06, ring)
    _circle(d, u, cx, cy, r, fill)


# ------------------------------------------------------------------ toolbar icons
def _tool_painter(name: str, ink: str) -> Painter:
    def paint(d: ImageDraw.ImageDraw, u: float) -> None:
        if name == "add":
            _circle(d, u, 0.5, 0.5, 0.44, BLUE)
            _line(d, u, [(0.5, 0.27), (0.5, 0.73)], WHITE, 0.1)
            _line(d, u, [(0.27, 0.5), (0.73, 0.5)], WHITE, 0.1)
        elif name == "resume":
            _circle(d, u, 0.5, 0.5, 0.44, GREEN)
            _play(d, u, 0.53, 0.5, 0.42, WHITE)
        elif name == "stop":
            _circle(d, u, 0.5, 0.5, 0.44, ORANGE)
            _pause(d, u, 0.5, 0.5, 0.46, WHITE)
        elif name == "stop_all":
            _circle(d, u, 0.62, 0.38, 0.32, "#F59E0B")
            _circle(d, u, 0.42, 0.58, 0.38, WHITE)
            _circle(d, u, 0.42, 0.58, 0.33, ORANGE)
            _pause(d, u, 0.42, 0.58, 0.36, WHITE)
        elif name == "delete":
            _trash(d, u, 0.16, 0.08, 0.68, RED)
        elif name == "delete_done":
            _trash(d, u, 0.06, 0.06, 0.62, RED)
            _badge(d, u, 0.72, 0.72, 0.22, GREEN, WHITE)
            _check(d, u, 0.72, 0.72, 0.24, WHITE, 0.07)
        elif name == "settings":
            cx = cy = 0.5
            for i in range(8):
                angle = i * math.pi / 4
                ca, sa = math.cos(angle), math.sin(angle)
                pts = []
                for dx, dy in ((-0.08, 0.30), (0.08, 0.30), (0.07, 0.46), (-0.07, 0.46)):
                    pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
                _poly(d, u, pts, ink)
            _circle(d, u, cx, cy, 0.33, ink)
            _circle(d, u, cx, cy, 0.13, "#00000000")
        elif name == "batch":
            _rrect(d, u, 0.26, 0.08, 0.92, 0.70, 0.09, "#A78BFA")
            _rrect(d, u, 0.08, 0.24, 0.74, 0.92, 0.09, VIOLET)
            _lines(d, u, 0.2, 0.44, 0.42, 0.14, 3, WHITE, 0.065)
        elif name == "queue_start":
            _lines(d, u, 0.08, 0.2, 0.56, 0.2, 3, ink, 0.09)
            _badge(d, u, 0.7, 0.7, 0.26, GREEN, WHITE)
            _play(d, u, 0.72, 0.7, 0.26, WHITE)
        elif name == "queue_stop":
            _lines(d, u, 0.08, 0.2, 0.56, 0.2, 3, ink, 0.09)
            _badge(d, u, 0.7, 0.7, 0.26, RED, WHITE)
            _rrect(d, u, 0.61, 0.61, 0.79, 0.79, 0.03, WHITE)
        elif name == "browser":
            _circle(d, u, 0.5, 0.5, 0.44, SKY)
            _circle(d, u, 0.5, 0.5, 0.34, None, WHITE, 0.06)
            d.ellipse(_box(u, 0.34, 0.06, 0.66, 0.94), outline=WHITE, width=int(0.06 * u))
            _line(d, u, [(0.08, 0.5), (0.92, 0.5)], WHITE, 0.06)
            _line(d, u, [(0.16, 0.3), (0.84, 0.3)], WHITE, 0.05)
            _line(d, u, [(0.16, 0.7), (0.84, 0.7)], WHITE, 0.05)
        elif name == "editor":
            _rrect(d, u, 0.06, 0.16, 0.94, 0.84, 0.1, TEAL)
            for fx in (0.14, 0.32, 0.5, 0.68, 0.86):
                _rrect(d, u, fx - 0.05, 0.21, fx + 0.05, 0.29, 0.02, WHITE)
                _rrect(d, u, fx - 0.05, 0.71, fx + 0.05, 0.79, 0.02, WHITE)
            _play(d, u, 0.52, 0.5, 0.3, WHITE)
        elif name == "folder":
            _folder(d, u, BLUE)
        elif name == "update":
            _circle(d, u, 0.5, 0.5, 0.44, GREEN)
            _line(d, u, [(0.5, 0.74), (0.5, 0.3)], WHITE, 0.1)
            _line(d, u, [(0.3, 0.48), (0.5, 0.27), (0.7, 0.48)], WHITE, 0.1)
        else:
            _circle(d, u, 0.5, 0.5, 0.4, SLATE)

    return paint


def _folder(d: ImageDraw.ImageDraw, u: float, color: str) -> None:
    _rrect(d, u, 0.06, 0.16, 0.48, 0.34, 0.06, color)
    _rrect(d, u, 0.06, 0.26, 0.94, 0.86, 0.08, color)
    _rrect(d, u, 0.06, 0.36, 0.94, 0.86, 0.08, _lighten(color, 0.18))


def _lighten(color: str, amount: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(v + (255 - v) * amount) for v in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


# ------------------------------------------------------------------ list / tree icons
_STATE_BADGES = {
    "complete": GREEN, "downloading": BLUE, "connecting": BLUE, "finishing": BLUE, "failed": RED, "paused": ORANGE,
    "stopping": ORANGE, "queued": SLATE, "retrying": AMBER, "cancelled": SLATE, "missing": AMBER,
}


def _file_painter(category: str, state: str, ring: str) -> Painter:
    color = CATEGORY_COLORS.get(category, SLATE)

    def paint(d: ImageDraw.ImageDraw, u: float) -> None:
        _rrect(d, u, 0.04, 0.04, 0.86, 0.86, 0.2, color)
        cx, cy = 0.45, 0.45
        if category == "video":
            _play(d, u, cx + 0.02, cy, 0.42, WHITE)
        elif category == "audio":
            _circle(d, u, 0.36, 0.6, 0.11, WHITE)
            _line(d, u, [(0.45, 0.6), (0.45, 0.2)], WHITE, 0.07)
            _poly(d, u, [(0.45, 0.18), (0.66, 0.26), (0.66, 0.36), (0.45, 0.30)], WHITE)
        elif category == "compressed":
            for i, y in enumerate((0.14, 0.26, 0.38)):
                x = 0.41 if i % 2 else 0.47
                _rrect(d, u, x, y, x + 0.06, y + 0.08, 0.01, WHITE)
            _rrect(d, u, 0.36, 0.5, 0.54, 0.72, 0.04, WHITE)
        elif category == "documents":
            _lines(d, u, 0.22, 0.27, 0.46, 0.14, 4, WHITE, 0.07)
        elif category == "programs":
            _rrect(d, u, 0.18, 0.2, 0.72, 0.7, 0.06, None, WHITE, 0.07)
            _line(d, u, [(0.18, 0.33), (0.72, 0.33)], WHITE, 0.07)
        else:
            for x in (0.27, 0.45, 0.63):
                _circle(d, u, x, 0.45, 0.06, WHITE)
        badge = _STATE_BADGES.get(state)
        if badge:
            bx = by = 0.76
            _badge(d, u, bx, by, 0.2, badge, ring)
            if state == "complete":
                _check(d, u, bx, by, 0.22, WHITE, 0.06)
            elif state in {"downloading", "connecting", "finishing"}:
                _arrow_down(d, u, bx, by, 0.24, WHITE, 0.06)
            elif state in {"failed", "cancelled"}:
                _cross(d, u, bx, by, 0.2, WHITE, 0.06)
            elif state in {"paused", "stopping"}:
                _pause(d, u, bx, by, 0.22, WHITE)
            elif state == "missing":
                _line(d, u, [(bx, by - 0.1), (bx, by + 0.02)], WHITE, 0.06)
                _circle(d, u, bx, by + 0.09, 0.03, WHITE)
            else:  # queued / retrying: a small clock
                _line(d, u, [(bx, by - 0.1), (bx, by), (bx + 0.08, by + 0.04)], WHITE, 0.05)

    return paint


def _tree_painter(name: str, ink: str) -> Painter:
    colors = {"all": BLUE, "unfinished": ORANGE, "finished": GREEN, "queues": VIOLET}

    def paint(d: ImageDraw.ImageDraw, u: float) -> None:
        if name in colors:
            _folder(d, u, colors[name])
            if name == "finished":
                _check(d, u, 0.5, 0.6, 0.3, WHITE, 0.09)
            elif name == "unfinished":
                _arrow_down(d, u, 0.5, 0.6, 0.34, WHITE, 0.09)
        elif name == "queue":
            _lines(d, u, 0.12, 0.25, 0.76, 0.25, 3, ink, 0.1)
        elif name in CATEGORY_COLORS:
            _file_painter(name, "", "#00000000")(d, u)
        else:
            _folder(d, u, SLATE)

    return paint


# ------------------------------------------------------------------ cache
class IconSet:
    """Creates PhotoImages on demand and keeps them alive (Tk drops unreferenced images)."""

    def __init__(self, master, *, ink: str, ring: str) -> None:
        self.master = master
        self.ink = ink
        self.ring = ring
        self._cache: dict[tuple, ImageTk.PhotoImage] = {}

    def _photo(self, key: tuple, size: int, paint: Painter, disabled: bool = False) -> ImageTk.PhotoImage:
        cache_key = (*key, size, disabled)
        photo = self._cache.get(cache_key)
        if photo is None:
            image = _render(size, paint)
            if disabled:
                alpha = image.getchannel("A").point(lambda v: int(v * 0.35))
                image = ImageEnhance.Color(image).enhance(0.0)
                image.putalpha(alpha)
            photo = ImageTk.PhotoImage(image, master=self.master)
            self._cache[cache_key] = photo
        return photo

    def tool(self, name: str, size: int, disabled: bool = False) -> ImageTk.PhotoImage:
        return self._photo(("tool", name), size, _tool_painter(name, self.ink), disabled)

    def file(self, category: str, state: str, size: int) -> ImageTk.PhotoImage:
        return self._photo(("file", category, state), size, _file_painter(category, state, self.ring))

    def tree(self, name: str, size: int) -> ImageTk.PhotoImage:
        return self._photo(("tree", name), size, _tree_painter(name, self.ink))


def render_png(kind: str, name: str, size: int, path: str, *, ink: str = "#475569", state: str = "") -> None:
    """Save one icon as a PNG (used to preview the icon set)."""
    painter = {"tool": lambda: _tool_painter(name, ink), "file": lambda: _file_painter(name, state, WHITE),
               "tree": lambda: _tree_painter(name, ink)}[kind]()
    _render(size, painter).save(path)
