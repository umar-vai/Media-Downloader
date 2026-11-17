from __future__ import annotations

import math
import tkinter as tk
import time
from typing import Callable

from PIL import Image, ImageEnhance, ImageTk

from edit_timeline import (GAP, KEEP, REMOVED, Clip, EditTimeline, clip_at, seq_from_source, sequence_duration,
                           sequence_layout, snap, source_from_seq)

# A dark, professional timeline (Premiere / Resolve / CapCut style) in every app theme.
BG = "#16171B"              # canvas
RULER_BG = "#1E1F24"
TRACK_BG = "#202127"        # empty track
BORDER = "#34363F"
TEXT = "#E6E8EE"
MUTED = "#8C90A0"
CLIP_EDGE = "#5B8CFF"       # clip outline
CLIP_HEAD = "#2F4C8F"       # clip title strip
AUDIO_CLIP = "#1D6E5A"      # audio-only clip background
CYAN = "#FF4D4D"            # playhead (red, like most editors)
PURPLE = "#F5C542"          # In / Out handles
SELECTED = "#FFB020"        # selected clip outline
CUT_LINE = "#FFFFFF"        # an editable cut between two clips
BLADE = "#FF4D4D"           # blade preview line
GAP_FILL = "#0B0B0D"        # deleted part that leaves a black gap
GAP_HATCH = "#26272D"
REMOVED_FILL = "#5A0B12"    # kept for callers; ripple-deleted parts are not drawn at all
SURFACE_2 = TRACK_BG
SURFACE_3 = BORDER

TOOL_SELECT = "select"
TOOL_BLADE = "blade"
SNAP_PX = 8
CUT_GRAB_PX = 6

RULER_H = 24
TRACK_TOP = 30
TRACK_BOTTOM = 104
HEAD_H = 14
TICK_STEPS = (0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)


def _label(seconds: float, step: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if step < 1:
        text = f"{minutes}:{secs:04.1f}"
    else:
        text = f"{minutes}:{int(secs):02d}"
    return f"{hours}:{text.zfill(5)}" if hours else text


class TimelineCanvas(tk.Canvas):
    """Magnetic timeline.

    Everything the caller passes in (playhead, In/Out, cuts) is *source* time. The canvas
    shows the *sequence*: the parts left after a ripple delete are packed together with no
    hole, the way Premiere's ripple delete or CapCut's magnetic main track work. A deleted
    part that leaves a gap shows as empty black space of the same length.
    """

    def __init__(
        self,
        master,
        *,
        on_seek: Callable[[float], None] | None = None,
        on_range_change: Callable[[float, float], None] | None = None,
        on_split: Callable[[float], None] | None = None,
        on_cut_moved: Callable[[], None] | None = None,
        on_selection_change: Callable[[], None] | None = None,
        on_context_menu: Callable[[object, int], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            bg=BG,
            highlightthickness=0,
            bd=0,
            height=132,
            cursor="hand2",
            **kwargs,
        )
        self.duration = 1.0
        self.start = 0.0
        self.end = 1.0
        self.playhead = 0.0
        # The view is in sequence (timeline) seconds.
        self.view_start = 0.0
        self.view_end = 1.0
        self._fit = True
        self.on_seek = on_seek
        self.on_range_change = on_range_change
        self.drag_target: str | None = None
        self.thumbnail_pils: list[Image.Image] = []
        self.thumbnail_times: list[float] = []
        self.thumbnail_refs: list[ImageTk.PhotoImage] = []
        self.waveform_pil: Image.Image | None = None
        self.waveform_ref: ImageTk.PhotoImage | None = None
        self._dim_cache: dict[int, Image.Image] = {}
        self._last_scrub_notify = 0.0

        # Blade editing: the cut list, the active tool and the selected segments.
        self.edit: EditTimeline | None = None
        self.layout: list[Clip] = sequence_layout(None, self.duration)
        self.tool = TOOL_SELECT
        self.snap_enabled = True
        self.selected: set[int] = set()
        self._anchor: int | None = None
        self._hover_x: float | None = None
        self._drag_cut: int | None = None
        self._drag_cut_origin: float | None = None
        self.on_split = on_split
        self.on_cut_moved = on_cut_moved
        self.on_selection_change = on_selection_change
        self.on_context_menu = on_context_menu
        self.enabled = True

        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<Control-Button-1>", lambda event: self._on_press(event, toggle=True))
        self.bind("<Shift-Button-1>", lambda event: self._on_press(event, extend=True))
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.bind("<Button-4>", lambda event: self._on_wheel(event, delta=120))
        self.bind("<Button-5>", lambda event: self._on_wheel(event, delta=-120))

    # ------------------------------------------------------------------ sequence mapping
    def _relayout(self) -> None:
        self.layout = sequence_layout(self.edit, self.duration)
        total = self.seq_total
        if self._fit or self.view_end - self.view_start >= total - 1e-6:
            self.view_start, self.view_end = 0.0, total
            return
        span = min(self.view_end - self.view_start, total)
        start = max(0.0, min(self.view_start, total - span))
        self.view_start, self.view_end = start, start + span

    @property
    def seq_total(self) -> float:
        return max(0.001, sequence_duration(self.layout))

    def to_seq(self, value: float) -> float:
        return seq_from_source(self.layout, value)

    def to_source(self, value: float) -> float:
        return source_from_seq(self.layout, value)

    # ------------------------------------------------------------------ blade editing API
    def set_edit(self, edit: EditTimeline | None) -> None:
        self.edit = edit
        self.selected.clear()
        self._anchor = None
        self.redraw()

    def set_tool(self, tool: str) -> None:
        self.tool = TOOL_BLADE if tool == TOOL_BLADE else TOOL_SELECT
        self.configure(cursor="crosshair" if self.tool == TOOL_BLADE else "hand2")
        self.redraw()

    def set_selection(self, indices: set[int] | list[int], *, notify: bool = True) -> None:
        count = len(self.edit.states) if self.edit else 0
        self.selected = {index for index in indices if 0 <= index < count}
        self.redraw()
        if notify and self.on_selection_change:
            self.on_selection_change()

    def select_all(self) -> None:
        if self.edit:
            self.set_selection({clip.index for clip in self.layout})

    def _editable_cuts(self) -> list[tuple[int, float]]:
        """Cuts between two clips that touch in the source too (a roll edit can move them)."""
        if not self.edit:
            return []
        result = []
        for left, right in zip(self.layout, self.layout[1:]):
            if right.index == left.index + 1 and left.index < len(self.edit.cuts):
                result.append((left.index, self.edit.cuts[left.index]))
        return result

    def _snap_targets(self, exclude_cut: int | None = None) -> list[float]:
        """Snap targets in sequence seconds: playhead, In/Out, ends and clip edges."""
        targets = [self.to_seq(self.playhead), self.to_seq(self.start), self.to_seq(self.end), 0.0, self.seq_total]
        for clip in self.layout:
            if exclude_cut is not None and clip.index in (exclude_cut, exclude_cut + 1):
                continue
            targets += [clip.seq_start, clip.seq_end]
        return targets

    def _seconds_per_px(self) -> float:
        left, right = self._x_bounds()
        return (self.view_end - self.view_start) / max(1.0, right - left)

    def snapped_time(self, x: float, *, exclude_cut: int | None = None, include_playhead: bool = True) -> float:
        value = self._x_to_seq(x)
        if self.snap_enabled:
            targets = self._snap_targets(exclude_cut)
            if not include_playhead:
                targets = targets[1:]
            value = snap(value, targets, SNAP_PX * self._seconds_per_px())
        return self.to_source(value)

    def _cut_near(self, x: float) -> int | None:
        best: int | None = None
        best_distance = float(CUT_GRAB_PX)
        for index, cut in self._editable_cuts():
            position = self.to_seq(cut)
            if not self.view_start <= position <= self.view_end:
                continue
            distance = abs(x - self._seq_to_x(position))
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    def clip_index_at(self, x: float) -> int | None:
        clip = clip_at(self.layout, self._x_to_seq(x))
        return clip.index if clip else None

    # ------------------------------------------------------------------ media / range / playhead
    def set_media(self, duration: float, start: float, end: float, playhead: float) -> None:
        self.duration = max(0.001, float(duration))
        self.start = max(0.0, min(float(start), self.duration))
        self.end = max(self.start + 0.001, min(float(end), self.duration))
        self.playhead = max(0.0, min(float(playhead), self.duration))
        self._fit = True
        self.redraw()

    def set_range(self, start: float, end: float, *, notify: bool = False) -> None:
        start = max(0.0, min(float(start), self.duration))
        end = max(start + 0.001, min(float(end), self.duration))
        self.start = start
        self.end = end
        if self.playhead < start:
            self.playhead = start
        elif self.playhead > end:
            self.playhead = end
        self.redraw()
        if notify and self.on_range_change:
            self.on_range_change(self.start, self.end)

    def set_playhead(self, value: float, *, notify: bool = False) -> None:
        self.playhead = max(0.0, min(float(value), self.duration))
        if not self._fit:
            span = self.view_end - self.view_start
            position = self.to_seq(self.playhead)
            if position < self.view_start or position > self.view_end:
                self.view_start = max(0.0, min(position - span * 0.1, self.seq_total - span))
                self.view_end = self.view_start + span
        self.redraw()
        if notify and self.on_seek:
            self.on_seek(self.playhead)

    def set_thumbnails(self, images: list[Image.Image], times: list[float] | None = None) -> None:
        self.thumbnail_pils = [image.copy() for image in images]
        if times and len(times) == len(images):
            self.thumbnail_times = [max(0.0, min(float(value), self.duration)) for value in times]
        elif images:
            count = len(images)
            self.thumbnail_times = [
                0.0 if count == 1 else self.duration * index / (count - 1)
                for index in range(count)
            ]
        else:
            self.thumbnail_times = []
        self.waveform_pil = None
        self._dim_cache = {}
        self.redraw()

    def set_waveform(self, image: Image.Image) -> None:
        self.waveform_pil = image.copy()
        self.thumbnail_pils = []
        self._dim_cache = {}
        self.redraw()

    # ------------------------------------------------------------------ coordinates
    def _x_bounds(self) -> tuple[float, float]:
        width = max(10, self.winfo_width())
        return 12.0, float(width - 12)

    def _seq_to_x(self, value: float) -> float:
        left, right = self._x_bounds()
        span = max(0.001, self.view_end - self.view_start)
        return left + ((float(value) - self.view_start) / span) * (right - left)

    def _x_to_seq(self, x: float) -> float:
        left, right = self._x_bounds()
        if right <= left:
            return self.view_start
        fraction = (max(left, min(float(x), right)) - left) / (right - left)
        return self.view_start + fraction * (self.view_end - self.view_start)

    def _time_to_x(self, value: float) -> float:
        """Source time to x (clamped to the view)."""
        position = max(self.view_start, min(self.to_seq(value), self.view_end))
        return self._seq_to_x(position)

    def _x_to_time(self, x: float) -> float:
        """x to source time."""
        return self.to_source(self._x_to_seq(x))

    def fit_view(self) -> None:
        self._fit = True
        self.redraw()

    def zoom(self, factor: float) -> None:
        factor = max(0.1, float(factor))
        total = self.seq_total
        current = max(0.001, self.view_end - self.view_start)
        min_span = min(total, max(0.5, total / 400.0))
        target = max(min_span, min(total, current / factor))
        center = max(self.view_start, min(self.to_seq(self.playhead), self.view_end))
        start = max(0.0, min(center - target / 2, total - target))
        self.view_start, self.view_end = start, start + target
        self._fit = target >= total - 1e-6
        self.redraw()

    def zoom_in(self) -> None:
        self.zoom(1.6)

    def zoom_out(self) -> None:
        self.zoom(1 / 1.6)

    # ------------------------------------------------------------------ drawing
    def redraw(self) -> None:
        self._relayout()
        self.delete("all")
        width = max(20, self.winfo_width())
        height = max(60, self.winfo_height())
        left, right = self._x_bounds()
        top, bottom = TRACK_TOP, TRACK_BOTTOM

        self.create_rectangle(0, 0, width, RULER_H, fill=RULER_BG, outline="")
        self.create_line(0, RULER_H, width, RULER_H, fill=BORDER)
        self.create_rectangle(left, top, right, bottom, fill=TRACK_BG, outline="")
        self._draw_ruler(left, right)

        self.thumbnail_refs = []
        self.waveform_ref = None
        self._draw_clips(top, bottom)
        self._draw_range(top, bottom)

        if self.tool == TOOL_BLADE and self._hover_x is not None:
            blade_time = self.snapped_time(self._hover_x)
            blade_x = self._time_to_x(blade_time)
            self.create_line(blade_x, top - 4, blade_x, bottom + 4, fill=BLADE, width=2, dash=(4, 2))
            self.create_text(blade_x + 4, top + 1, text="✂", fill=BLADE, anchor="nw",
                             font=("Segoe UI Symbol", 11, "bold"))

        position = self.to_seq(self.playhead)
        if self.view_start <= position <= self.view_end:
            play_x = self._seq_to_x(position)
            self.create_line(play_x, 2, play_x, bottom + 6, fill=CYAN, width=2)
            self.create_polygon(play_x - 7, 2, play_x + 7, 2, play_x + 7, 12, play_x, 19, play_x - 7, 12,
                                fill=CYAN, outline=CYAN)

        if self.edit is not None and not self.layout:
            self.create_text(width / 2, (top + bottom) / 2, text="Everything was deleted • Ctrl+Z to undo",
                             fill=MUTED, font=("Segoe UI", 9, "bold"))

        if self.tool == TOOL_BLADE:
            hint = "Blade (B): click a clip to cut it • V = selection tool • Ctrl+K cuts at the playhead"
        elif self.edit is not None and self.edit.cuts:
            hint = ("Click a clip to select (Ctrl/Shift for more) • Delete removes it and the rest snaps together • "
                    "drag a white cut to trim • right-click for more")
        else:
            hint = "B = blade to cut • drag the yellow In/Out handles to trim • Space = play/pause • Ctrl+wheel = zoom"
        self.create_text(width / 2, min(height - 8, bottom + 15), text=hint, fill=MUTED, font=("Segoe UI", 8))

    def _draw_ruler(self, left: float, right: float) -> None:
        span = max(0.001, self.view_end - self.view_start)
        px_per_second = (right - left) / span
        step = next((value for value in TICK_STEPS if value * px_per_second >= 80), TICK_STEPS[-1])
        minor = step / 5
        first = math.floor(self.view_start / minor) * minor
        count = int(span / minor) + 2
        for index in range(count):
            value = first + index * minor
            if value < self.view_start - 1e-9 or value > self.view_end + 1e-9:
                continue
            x = self._seq_to_x(value)
            major = abs(value / step - round(value / step)) < 1e-6
            self.create_line(x, RULER_H - (10 if major else 4), x, RULER_H, fill=MUTED if major else BORDER)
            if major:
                self.create_text(x + 3, 3, text=_label(value, step), fill=MUTED, anchor="nw",
                                 font=("Segoe UI", 8))

    def _strip_image(self, source: float, end: float, width: int, height: int) -> list[tuple[float, Image.Image]]:
        """Thumbnail tiles (x offset in px, image) for source time ``source``..``end``."""
        tiles: list[tuple[float, Image.Image]] = []
        if width <= 0:
            return tiles
        span = max(1e-6, end - source)
        if self.waveform_pil is not None:
            image = self.waveform_pil
            image_w, image_h = image.size
            a = int(source / self.duration * image_w)
            b = int(end / self.duration * image_w)
            a = max(0, min(a, image_w - 1))
            b = max(a + 1, min(b, image_w))
            tiles.append((0.0, image.crop((a, 0, b, image_h)).resize((width, height))))
            return tiles
        samples = list(zip(self.thumbnail_times, self.thumbnail_pils))
        for index, (sample_time, image) in enumerate(samples):
            before = samples[index - 1][0] if index > 0 else 0.0
            after = samples[index + 1][0] if index + 1 < len(samples) else self.duration
            a = 0.0 if index == 0 else (before + sample_time) / 2
            b = self.duration if index == len(samples) - 1 else (sample_time + after) / 2
            a, b = max(a, source), min(b, end)
            if b <= a:
                continue
            x1 = (a - source) / span * width
            x2 = (b - source) / span * width
            tile_w = max(1, int(round(x2 - x1)) + 1)
            # Keep the frame's shape: crop the middle instead of squeezing it.
            iw, ih = image.size
            target_ratio = tile_w / max(1, height)
            if iw / max(1, ih) > target_ratio:
                crop_w = max(1, int(ih * target_ratio))
                box = ((iw - crop_w) // 2, 0, (iw - crop_w) // 2 + crop_w, ih)
            else:
                crop_h = max(1, int(iw / max(target_ratio, 1e-6)))
                box = (0, (ih - crop_h) // 2, iw, (ih - crop_h) // 2 + crop_h)
            tiles.append((x1, image.crop(box).resize((tile_w, height))))
        return tiles

    def _draw_clips(self, top: float, bottom: float) -> None:
        body_top = top + HEAD_H
        body_h = int(bottom - body_top)
        cut_xs = {index for index, _cut in self._editable_cuts()}
        for clip in self.layout:
            a = max(clip.seq_start, self.view_start)
            b = min(clip.seq_end, self.view_end)
            if b <= a:
                continue
            x1, x2 = self._seq_to_x(a), self._seq_to_x(b)
            # A 1 px gap between clips so each one reads as its own block.
            gx1 = x1 + (1 if clip.seq_start >= self.view_start and clip.seq_start > 0 else 0)
            if clip.state == GAP:
                self.create_rectangle(gx1, top, x2, bottom, fill=GAP_FILL, outline="")
                step = 10
                x = gx1 - (bottom - top)
                while x < x2:
                    xa, ya, xb, yb = x, bottom, x + (bottom - top), top
                    if xa < gx1:
                        ya, xa = bottom - (gx1 - xa), gx1
                    if xb > x2:
                        yb, xb = top + (xb - x2), x2
                    if xb > xa:
                        self.create_line(xa, ya, xb, yb, fill=GAP_HATCH)
                    x += step
                if x2 - gx1 > 50:
                    self.create_text((gx1 + x2) / 2, (top + bottom) / 2, text="Gap", fill=MUTED,
                                     font=("Segoe UI", 8, "bold"))
            else:
                base = AUDIO_CLIP if self.waveform_pil is not None else "#111318"
                self.create_rectangle(gx1, top, x2, bottom, fill=base, outline="")
                source_a = clip.start + (a - clip.seq_start)
                source_b = clip.start + (b - clip.seq_start)
                for offset, tile in self._strip_image(source_a, source_b, int(round(x2 - gx1)), body_h):
                    photo = ImageTk.PhotoImage(tile)
                    self.thumbnail_refs.append(photo)
                    self.create_image(gx1 + offset, body_top, anchor="nw", image=photo)
                self.create_rectangle(gx1, top, x2, body_top, fill=CLIP_HEAD, outline="")
                if x2 - gx1 > 46:
                    self.create_text(gx1 + 5, top + HEAD_H / 2, text=f"Clip {clip.index + 1}  ·  "
                                     f"{_label(clip.duration, 0.5 if clip.duration < 60 else 1)}",
                                     fill=TEXT, anchor="w", font=("Segoe UI", 8))
                self.create_rectangle(gx1, top, x2, bottom, outline=CLIP_EDGE, width=1)
            if clip.index in self.selected:
                self.create_rectangle(gx1 + 1, top + 1, x2 - 1, bottom - 1, outline=SELECTED, width=3)
            # The cut after this clip, when it can be dragged (roll edit).
            if clip.index in cut_xs and self.view_start <= clip.seq_end <= self.view_end:
                x = self._seq_to_x(clip.seq_end)
                self.create_line(x, top - 2, x, bottom + 2, fill=CUT_LINE, width=2)
                self.create_polygon(x - 4, bottom + 2, x + 4, bottom + 2, x, bottom - 4, fill=CUT_LINE,
                                    outline=CUT_LINE)

    def _draw_range(self, top: float, bottom: float) -> None:
        """Darken what is outside In/Out and draw the two yellow handles."""
        left, right = self._x_bounds()
        start = self.to_seq(self.start)
        end = self.to_seq(self.end)
        for a, b in ((self.view_start, start), (end, self.view_end)):
            a, b = max(a, self.view_start), min(b, self.view_end)
            if b > a:
                self.create_rectangle(self._seq_to_x(a), top, self._seq_to_x(b), bottom, fill="#000000",
                                      stipple="gray50", outline="")
        for value, side in ((start, -1), (end, 1)):
            if not self.view_start <= value <= self.view_end:
                continue
            x = self._seq_to_x(value)
            self.create_line(x, top - 3, x, bottom + 3, fill=PURPLE, width=3)
            self.create_polygon(x, top - 3, x - 8 * side, top - 3, x, top + 7, fill=PURPLE, outline=PURPLE)
            self.create_polygon(x, bottom + 3, x - 8 * side, bottom + 3, x, bottom - 7, fill=PURPLE,
                                outline=PURPLE)

    # ------------------------------------------------------------------ mouse
    def _closest_target(self, x: float) -> str:
        distances: dict[str, float] = {}
        # The playhead comes first, so it wins a tie with an In/Out handle under it.
        for name, value in (("playhead", self.playhead), ("start", self.start), ("end", self.end)):
            position = self.to_seq(value)
            if self.view_start <= position <= self.view_end:
                distances[name] = abs(x - self._seq_to_x(position))
        if distances:
            target, distance = min(distances.items(), key=lambda item: item[1])
            if distance <= 10:
                return target
        return "playhead"

    @staticmethod
    def _in_strip(y: float) -> bool:
        return TRACK_TOP - 2 <= y <= TRACK_BOTTOM + 2

    def _on_press(self, event, *, toggle: bool = False, extend: bool = False) -> str | None:
        if not self.enabled:
            return "break"
        self.focus_set()
        in_strip = self._in_strip(event.y)
        if self.tool == TOOL_BLADE and in_strip and self.edit is not None:
            if self.on_split:
                self.on_split(self.snapped_time(event.x))
            self.drag_target = None
            return "break"

        target = self._closest_target(event.x) if in_strip else "playhead"
        if target == "playhead" and in_strip:
            cut = self._cut_near(event.x)
            if cut is not None and self.edit is not None:
                self.drag_target = "cut"
                self._drag_cut = cut
                self._drag_cut_origin = self.edit.cuts[cut]
                return "break"

        if in_strip and self.edit is not None and target == "playhead":
            index = self.clip_index_at(event.x)
            if index is not None:
                if toggle:
                    selection = set(self.selected)
                    selection.symmetric_difference_update({index})
                    self._anchor = index
                elif extend and self._anchor is not None:
                    low, high = sorted((self._anchor, index))
                    visible = {clip.index for clip in self.layout}
                    selection = {i for i in range(low, high + 1) if i in visible}
                else:
                    selection = {index}
                    self._anchor = index
                self.set_selection(selection)

        self.drag_target = target
        self._apply_drag(event.x, notify=False)
        return "break"

    def _on_drag(self, event) -> None:
        if not self.enabled or self.drag_target is None:
            return
        if self.drag_target == "cut":
            if self.edit is not None and self._drag_cut is not None:
                value = self.snapped_time(event.x, exclude_cut=self._drag_cut)
                self.edit.move_cut(self._drag_cut, value)
                self.redraw()
            return
        notify = False
        if self.drag_target == "playhead":
            now = time.monotonic()
            if now - self._last_scrub_notify >= 0.04:
                self._last_scrub_notify = now
                notify = True
        self._apply_drag(event.x, notify=notify)

    def _on_release(self, event) -> None:
        if not self.enabled or self.drag_target is None:
            self.drag_target = None
            return
        if self.drag_target == "cut":
            moved = (
                self.edit is not None
                and self._drag_cut is not None
                and self._drag_cut_origin is not None
                and abs(self.edit.cuts[self._drag_cut] - self._drag_cut_origin) > 1e-6
            )
            self.drag_target = None
            self._drag_cut = None
            self._drag_cut_origin = None
            if moved and self.on_cut_moved:
                self.on_cut_moved()
            return
        self._apply_drag(event.x, notify=True)
        self.drag_target = None

    def _on_motion(self, event) -> None:
        if self.tool == TOOL_BLADE:
            self._hover_x = event.x if self._in_strip(event.y) else None
            self.redraw()
        elif self.drag_target is None:
            near_cut = self._in_strip(event.y) and self._cut_near(event.x) is not None
            self.configure(cursor="sb_h_double_arrow" if near_cut else "hand2")

    def _on_leave(self, _event) -> None:
        if self._hover_x is not None:
            self._hover_x = None
            self.redraw()

    def _on_right_click(self, event) -> str | None:
        if not self.enabled or self.edit is None or not self.on_context_menu:
            return None
        index = self.clip_index_at(event.x)
        if index is None:
            return "break"
        if index not in self.selected:
            self._anchor = index
            self.set_selection({index})
        self.on_context_menu(event, index)
        return "break"

    def _on_wheel(self, event, delta: int | None = None) -> str:
        delta = event.delta if delta is None else delta
        span = self.view_end - self.view_start
        total = self.seq_total
        if span >= total - 0.001:
            return "break"
        shift = -span * 0.1 if delta > 0 else span * 0.1
        start = max(0.0, min(self.view_start + shift, total - span))
        self.view_start, self.view_end = start, start + span
        self._fit = False
        self.redraw()
        return "break"

    def _on_ctrl_wheel(self, event) -> str:
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        return "break"

    def _apply_drag(self, x: float, *, notify: bool) -> None:
        position = self._x_to_seq(x)
        if self.drag_target in {"start", "end"} and self.snap_enabled:
            position = snap(position, [clip.seq_start for clip in self.layout] +
                            [clip.seq_end for clip in self.layout] + [self.to_seq(self.playhead)],
                            SNAP_PX * self._seconds_per_px())
        value = self.to_source(position)
        minimum_gap = min(0.1, self.duration / 100)

        if self.drag_target == "start":
            self.start = max(0.0, min(value, self.end - minimum_gap))
            if self.playhead < self.start:
                self.playhead = self.start
            self.redraw()
            if notify and self.on_range_change:
                self.on_range_change(self.start, self.end)
        elif self.drag_target == "end":
            self.end = min(self.duration, max(value, self.start + minimum_gap))
            if self.playhead > self.end:
                self.playhead = self.end
            self.redraw()
            if notify and self.on_range_change:
                self.on_range_change(self.start, self.end)
        else:
            self.playhead = max(0.0, min(value, self.duration))
            self.redraw()
            if notify and self.on_seek:
                self.on_seek(self.playhead)
