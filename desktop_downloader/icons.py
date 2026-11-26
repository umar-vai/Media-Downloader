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
# Small, flat glyphs in the spirit of a classic Windows toolbar: one shape, one plain colour,
# no coloured discs or badges behind them.
TOOL_GREEN = "#2E8B3E"
TOOL_RED = "#C42B1C"
TOOL_BLUE = "#2F6FBA"


def _page(d: ImageDraw.ImageDraw, u: float, x0: float, y0: float, x1: float, y1: float, ink: str,
          fill: str = "#FFFFFF") -> None:
    """A sheet of paper with a folded corner, drawn as an outline."""
    fold = (x1 - x0) * 0.32
    pts = [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)]
    d.polygon([(px_ * u, py_ * u) for px_, py_ in pts], fill=fill, outline=ink, width=max(1, int(0.07 * u)))
    _line(d, u, [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], ink, 0.06)


def _tool_painter(name: str, ink: str) -> Painter:
    def paint(d: ImageDraw.ImageDraw, u: float) -> None:
        if name == "add":
            _page(d, u, 0.14, 0.06, 0.74, 0.94, ink)
            _line(d, u, [(0.72, 0.42), (0.72, 0.94)], TOOL_GREEN, 0.15)
            _line(d, u, [(0.46, 0.68), (0.98, 0.68)], TOOL_GREEN, 0.15)
        elif name == "batch":
            _page(d, u, 0.30, 0.04, 0.92, 0.74, ink)
            _page(d, u, 0.10, 0.24, 0.72, 0.96, ink)
            _lines(d, u, 0.22, 0.52, 0.36, 0.14, 3, ink, 0.06)
        elif name == "resume":
            _poly(d, u, [(0.22, 0.10), (0.22, 0.90), (0.86, 0.50)], TOOL_GREEN)
        elif name == "stop":
            _rrect(d, u, 0.18, 0.18, 0.82, 0.82, 0.05, TOOL_RED)
        elif name == "queue_start":
            _poly(d, u, [(0.06, 0.16), (0.06, 0.84), (0.52, 0.50)], TOOL_GREEN)
            _poly(d, u, [(0.48, 0.16), (0.48, 0.84), (0.94, 0.50)], TOOL_GREEN)
        elif name in {"stop_all", "queue_stop"}:
            _rrect(d, u, 0.34, 0.08, 0.92, 0.66, 0.04, "#E08A80")
            _rrect(d, u, 0.08, 0.34, 0.66, 0.92, 0.04, TOOL_RED)
        elif name == "delete":
            _line(d, u, [(0.18, 0.18), (0.82, 0.82)], TOOL_RED, 0.17)
            _line(d, u, [(0.82, 0.18), (0.18, 0.82)], TOOL_RED, 0.17)
        elif name == "delete_done":
            _line(d, u, [(0.08, 0.08), (0.62, 0.62)], TOOL_RED, 0.15)
            _line(d, u, [(0.62, 0.08), (0.08, 0.62)], TOOL_RED, 0.15)
            _check(d, u, 0.70, 0.74, 0.46, TOOL_GREEN, 0.13)
        elif name == "cleanup":
            for y in (0.20, 0.42, 0.64):
                _line(d, u, [(0.08, y), (0.56 if y > 0.5 else 0.92, y)], ink, 0.09)
            _line(d, u, [(0.58, 0.58), (0.94, 0.94)], TOOL_RED, 0.14)
            _line(d, u, [(0.94, 0.58), (0.58, 0.94)], TOOL_RED, 0.14)
        elif name == "settings":
            cx = cy = 0.5
            for i in range(8):
                angle = i * math.pi / 4
                ca, sa = math.cos(angle), math.sin(angle)
                pts = []
                for dx, dy in ((-0.09, 0.28), (0.09, 0.28), (0.08, 0.46), (-0.08, 0.46)):
                    pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
                _poly(d, u, pts, ink)
            _circle(d, u, cx, cy, 0.33, ink)
            _circle(d, u, cx, cy, 0.13, "#00000000")
        elif name == "browser":
            _circle(d, u, 0.5, 0.5, 0.43, None, TOOL_BLUE, 0.08)
            d.ellipse(_box(u, 0.31, 0.07, 0.69, 0.93), outline=TOOL_BLUE, width=max(1, int(0.07 * u)))
            _line(d, u, [(0.07, 0.5), (0.93, 0.5)], TOOL_BLUE, 0.07)
        elif name == "editor":
            _rrect(d, u, 0.06, 0.18, 0.94, 0.82, 0.06, ink)
            for fx in (0.18, 0.39, 0.61, 0.82):
                _rrect(d, u, fx - 0.06, 0.24, fx + 0.06, 0.32, 0.01, WHITE)
                _rrect(d, u, fx - 0.06, 0.68, fx + 0.06, 0.76, 0.01, WHITE)
            _rrect(d, u, 0.12, 0.38, 0.88, 0.62, 0.02, "#D7E6F5")
        elif name == "record":
            _rrect(d, u, 0.04, 0.10, 0.96, 0.74, 0.06, ink)
            _rrect(d, u, 0.11, 0.17, 0.89, 0.67, 0.02, WHITE)
            _rrect(d, u, 0.40, 0.74, 0.60, 0.86, 0.0, ink)
            _rrect(d, u, 0.26, 0.86, 0.74, 0.94, 0.03, ink)
            _circle(d, u, 0.5, 0.42, 0.17, TOOL_RED)
        elif name == "mic":
            _rrect(d, u, 0.36, 0.06, 0.64, 0.56, 0.14, TOOL_RED)
            d.arc(_box(u, 0.22, 0.28, 0.78, 0.78), 0, 180, fill=ink, width=max(1, int(0.07 * u)))
            _line(d, u, [(0.5, 0.78), (0.5, 0.92)], ink, 0.07)
            _line(d, u, [(0.34, 0.93), (0.66, 0.93)], ink, 0.07)
        elif name == "folder":
            _folder(d, u, BLUE)
        elif name == "update":
            _line(d, u, [(0.5, 0.08), (0.5, 0.70)], TOOL_GREEN, 0.15)
            _line(d, u, [(0.20, 0.44), (0.5, 0.76), (0.80, 0.44)], TOOL_GREEN, 0.15)
            _line(d, u, [(0.12, 0.92), (0.88, 0.92)], ink, 0.08)
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


# Classic Windows folder colours (the manila folder of Explorer's navigation pane).
FOLDER_BACK = "#DDA82E"
FOLDER_FRONT = "#F4CF6B"


def _plain_folder(d: ImageDraw.ImageDraw, u: float) -> None:
    _rrect(d, u, 0.06, 0.16, 0.46, 0.32, 0.04, FOLDER_BACK)
    _rrect(d, u, 0.06, 0.24, 0.94, 0.86, 0.05, FOLDER_BACK)
    _rrect(d, u, 0.06, 0.36, 0.94, 0.86, 0.05, FOLDER_FRONT)


def _tree_painter(name: str, ink: str) -> Painter:
    """Sidebar icons in the classic Windows style: a plain folder for the groups and one small,
    single-purpose glyph per file type, so each category reads at a glance."""
    line = "#5A5A5A"
    blue = "#2F6FBA"

    def paint(d: ImageDraw.ImageDraw, u: float) -> None:
        if name == "queue":
            _lines(d, u, 0.12, 0.25, 0.76, 0.25, 3, ink, 0.1)
        elif name == "video":
            # A film frame: dark strip with sprocket holes and a blue picture area.
            _rrect(d, u, 0.04, 0.16, 0.96, 0.84, 0.06, "#3C3C3C")
            for x in (0.1, 0.3, 0.5, 0.7):
                _rrect(d, u, x, 0.2, x + 0.12, 0.28, 0.01, WHITE)
                _rrect(d, u, x, 0.72, x + 0.12, 0.8, 0.01, WHITE)
            _rrect(d, u, 0.1, 0.34, 0.9, 0.66, 0.02, "#5B9BD5")
            _play(d, u, 0.52, 0.5, 0.26, WHITE)
        elif name == "audio":
            # A beamed pair of eighth notes.
            _circle(d, u, 0.26, 0.78, 0.15, blue)
            _circle(d, u, 0.74, 0.68, 0.15, blue)
            _line(d, u, [(0.38, 0.78), (0.38, 0.18)], blue, 0.09)
            _line(d, u, [(0.86, 0.68), (0.86, 0.08)], blue, 0.09)
            _poly(d, u, [(0.34, 0.14), (0.9, 0.04), (0.9, 0.2), (0.34, 0.3)], blue)
        elif name == "compressed":
            # A zip archive: a yellow box with a zipper down the middle.
            _rrect(d, u, 0.12, 0.08, 0.88, 0.92, 0.06, FOLDER_FRONT, FOLDER_BACK, 0.06)
            for i, y in enumerate((0.12, 0.22, 0.32, 0.42)):
                x = 0.44 if i % 2 else 0.5
                _rrect(d, u, x, y, x + 0.08, y + 0.08, 0.01, line)
            _rrect(d, u, 0.4, 0.54, 0.6, 0.74, 0.03, line)
        elif name == "documents":
            _page(d, u, 0.16, 0.04, 0.84, 0.96, line)
            _lines(d, u, 0.28, 0.46, 0.44, 0.14, 3, blue, 0.07)
        elif name == "programs":
            # An application window: white body with a blue title bar.
            _rrect(d, u, 0.06, 0.14, 0.94, 0.86, 0.04, WHITE, line, 0.06)
            _rrect(d, u, 0.06, 0.14, 0.94, 0.34, 0.04, blue)
            _rrect(d, u, 0.72, 0.19, 0.86, 0.29, 0.01, WHITE)
        elif name == "misc":
            _page(d, u, 0.16, 0.04, 0.84, 0.96, line)
        elif name == "unfinished":
            _plain_folder(d, u)
            _arrow_down(d, u, 0.62, 0.62, 0.42, blue, 0.11)
        elif name == "finished":
            _plain_folder(d, u)
            _check(d, u, 0.6, 0.62, 0.46, "#2E8B3E", 0.11)
        else:
            _plain_folder(d, u)

    return paint


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
