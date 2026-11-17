from __future__ import annotations

import tkinter as tk
import time
from typing import Callable

from PIL import Image, ImageTk

from edit_timeline import GAP, KEEP, REMOVED, EditTimeline, snap
from theme import C, IS_DARK

# Classic Windows colours: a white track, the system highlight blue for the selection and its
# handles, and a plain black (white in the dark theme) playhead.
BG = C["SURFACE"]
SURFACE_2 = C["SURFACE_2"]
SURFACE_3 = C["SURFACE_3"]
BORDER = C["BORDER"]
TEXT = C["TEXT"]
MUTED = C["MUTED"]
CYAN = C["TEXT"]                                   # playhead
PURPLE = C["ACCENT"] if IS_DARK else C["SELECT"]   # selection handles
CUT_LINE = "#FFD400"        # blade cuts
BLADE = "#E81123"           # blade preview line
SELECTED = "#FF8C00"        # selected segment outline
REMOVED_FILL = "#5A0B12"    # ripple-deleted segment
GAP_FILL = "#000000"        # deleted segment that leaves a black gap

TOOL_SELECT = "select"
TOOL_BLADE = "blade"
SNAP_PX = 8
CUT_GRAB_PX = 6


class TimelineCanvas(tk.Canvas):
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
            highlightthickness=1,
            highlightbackground=BORDER,
            bd=0,
            height=124,
            cursor="hand2",
            **kwargs,
        )
        self.duration = 1.0
        self.start = 0.0
        self.end = 1.0
        self.playhead = 0.0
        self.view_start = 0.0
        self.view_end = 1.0
        self.on_seek = on_seek
        self.on_range_change = on_range_change
        self.drag_target: str | None = None
        self.thumbnail_pils: list[Image.Image] = []
        self.thumbnail_times: list[float] = []
        self.thumbnail_refs: list[ImageTk.PhotoImage] = []
        self.waveform_pil: Image.Image | None = None
        self.waveform_ref: ImageTk.PhotoImage | None = None
        self._last_scrub_notify = 0.0

        # Blade editing: the cut list, the active tool and the selected segments.
        self.edit: EditTimeline | None = None
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
            self.set_selection(set(range(len(self.edit.states))))

    def _snap_targets(self, exclude_cut: int | None = None) -> list[float]:
        targets = [self.playhead, self.start, self.end, 0.0, self.duration]
        if self.edit:
            targets += [cut for index, cut in enumerate(self.edit.cuts) if index != exclude_cut]
        return targets

    def _seconds_per_px(self) -> float:
        left, right = self._x_bounds()
        return (self.view_end - self.view_start) / max(1.0, right - left)

    def snapped_time(self, x: float, *, exclude_cut: int | None = None, include_playhead: bool = True) -> float:
        value = self._x_to_time(x)
        if not self.snap_enabled:
            return value
        targets = self._snap_targets(exclude_cut)
        if not include_playhead:
            targets = targets[1:]
        return snap(value, targets, SNAP_PX * self._seconds_per_px())

    def _cut_near(self, x: float) -> int | None:
        if not self.edit or not self.edit.cuts:
            return None
        best: int | None = None
        best_distance = float(CUT_GRAB_PX)
        for index, cut in enumerate(self.edit.cuts):
            if not self.view_start <= cut <= self.view_end:
                continue
            distance = abs(x - self._time_to_x(cut))
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    def set_media(self, duration: float, start: float, end: float, playhead: float) -> None:
        self.duration = max(0.001, float(duration))
        self.start = max(0.0, min(float(start), self.duration))
        self.end = max(self.start + 0.001, min(float(end), self.duration))
        self.playhead = max(0.0, min(float(playhead), self.duration))
        self.view_start = 0.0
        self.view_end = self.duration
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
        if self.view_end - self.view_start < self.duration - 0.001:
            span = self.view_end - self.view_start
            if self.playhead < self.view_start or self.playhead > self.view_end:
                center = self.playhead
                self.view_start = max(0.0, min(center - span / 2, self.duration - span))
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
        self.redraw()

    def set_waveform(self, image: Image.Image) -> None:
        self.waveform_pil = image.copy()
        self.thumbnail_pils = []
        self.redraw()

    def _x_bounds(self) -> tuple[float, float]:
        width = max(10, self.winfo_width())
        return 18.0, float(width - 18)

    def _time_to_x(self, value: float) -> float:
        left, right = self._x_bounds()
        span = max(0.001, self.view_end - self.view_start)
        clipped = max(self.view_start, min(float(value), self.view_end))
        return left + ((clipped - self.view_start) / span) * (right - left)

    def _x_to_time(self, x: float) -> float:
        left, right = self._x_bounds()
        if right <= left:
            return self.view_start
        fraction = (max(left, min(float(x), right)) - left) / (right - left)
        return self.view_start + fraction * (self.view_end - self.view_start)

    def fit_view(self) -> None:
        self.view_start = 0.0
        self.view_end = self.duration
        self.redraw()

    def zoom(self, factor: float) -> None:
        factor = max(0.1, float(factor))
        current = max(0.001, self.view_end - self.view_start)
        min_span = min(self.duration, max(1.0, self.duration / 240.0))
        target = max(min_span, min(self.duration, current / factor))
        center = max(self.view_start, min(self.playhead, self.view_end))
        start = center - target / 2
        start = max(0.0, min(start, self.duration - target))
        self.view_start = start
        self.view_end = start + target
        self.redraw()

    def zoom_in(self) -> None:
        self.zoom(1.6)

    def zoom_out(self) -> None:
        self.zoom(1 / 1.6)

    def redraw(self) -> None:
        self.delete("all")
        width = max(20, self.winfo_width())
        left, right = self._x_bounds()
        strip_top = 10
        strip_bottom = 74
        ruler_y = 94
        visible_span = max(0.001, self.view_end - self.view_start)

        self.create_rectangle(left, strip_top, right, strip_bottom, fill=SURFACE_2, outline=BORDER)

        self.thumbnail_refs = []
        self.waveform_ref = None
        if self.waveform_pil is not None:
            available = max(1, int(right - left))
            tile_h = strip_bottom - strip_top
            source = self.waveform_pil
            source_w, source_h = source.size
            crop_left = int((self.view_start / self.duration) * source_w)
            crop_right = int((self.view_end / self.duration) * source_w)
            crop_left = max(0, min(crop_left, source_w - 1))
            crop_right = max(crop_left + 1, min(crop_right, source_w))
            waveform = source.crop((crop_left, 0, crop_right, source_h)).resize((available, tile_h))
            self.waveform_ref = ImageTk.PhotoImage(waveform)
            self.create_image(left, strip_top, anchor="nw", image=self.waveform_ref)
        elif self.thumbnail_pils:
            tile_h = strip_bottom - strip_top
            samples = list(zip(self.thumbnail_times, self.thumbnail_pils))
            if not samples:
                samples = [
                    (
                        0.0 if len(self.thumbnail_pils) == 1 else self.duration * index / (len(self.thumbnail_pils) - 1),
                        image,
                    )
                    for index, image in enumerate(self.thumbnail_pils)
                ]
            for index, (sample_time, source) in enumerate(samples):
                previous_time = samples[index - 1][0] if index > 0 else 0.0
                next_time = samples[index + 1][0] if index + 1 < len(samples) else self.duration
                interval_start = 0.0 if index == 0 else (previous_time + sample_time) / 2
                interval_end = self.duration if index == len(samples) - 1 else (sample_time + next_time) / 2
                visible_start = max(interval_start, self.view_start)
                visible_end = min(interval_end, self.view_end)
                if visible_end <= visible_start:
                    continue
                x1 = self._time_to_x(visible_start)
                x2 = self._time_to_x(visible_end)
                tile_w = max(1, int(x2 - x1) + 1)
                image = source.copy().resize((tile_w, tile_h))
                canvas_image = ImageTk.PhotoImage(image)
                self.thumbnail_refs.append(canvas_image)
                self.create_image(x1, strip_top, anchor="nw", image=canvas_image)

        visible_selection_start = max(self.start, self.view_start)
        visible_selection_end = min(self.end, self.view_end)
        if visible_selection_end > visible_selection_start:
            start_x = self._time_to_x(visible_selection_start)
            end_x = self._time_to_x(visible_selection_end)
            if self.start > self.view_start:
                self.create_rectangle(left, strip_top, start_x, strip_bottom, fill="#020712", stipple="gray50", outline="")
            if self.end < self.view_end:
                self.create_rectangle(end_x, strip_top, right, strip_bottom, fill="#020712", stipple="gray50", outline="")
            self.create_rectangle(start_x, strip_top, end_x, strip_bottom, outline=PURPLE, width=2)
        else:
            self.create_rectangle(left, strip_top, right, strip_bottom, fill="#020712", stipple="gray50", outline="")
            start_x = self._time_to_x(self.start)
            end_x = self._time_to_x(self.end)

        self._draw_segments(strip_top, strip_bottom)

        if self.view_start <= self.start <= self.view_end:
            start_x = self._time_to_x(self.start)
            self.create_line(start_x, strip_top - 2, start_x, strip_bottom + 9, fill=PURPLE, width=4)
            self.create_polygon(
                start_x - 7,
                strip_top - 2,
                start_x + 7,
                strip_top - 2,
                start_x,
                strip_top + 8,
                fill=PURPLE,
                outline=PURPLE,
            )
        if self.view_start <= self.end <= self.view_end:
            end_x = self._time_to_x(self.end)
            self.create_line(end_x, strip_top - 2, end_x, strip_bottom + 9, fill=PURPLE, width=4)
            self.create_polygon(
                end_x - 7,
                strip_top - 2,
                end_x + 7,
                strip_top - 2,
                end_x,
                strip_top + 8,
                fill=PURPLE,
                outline=PURPLE,
            )

        if self.view_start <= self.playhead <= self.view_end:
            play_x = self._time_to_x(self.playhead)
            self.create_line(play_x, strip_top - 6, play_x, ruler_y + 12, fill=CYAN, width=2)
            self.create_polygon(
                play_x - 6,
                strip_top - 6,
                play_x + 6,
                strip_top - 6,
                play_x,
                strip_top + 2,
                fill=CYAN,
                outline=CYAN,
            )

        tick_count = 6
        for index in range(tick_count + 1):
            fraction = index / tick_count
            x = left + fraction * (right - left)
            seconds = self.view_start + fraction * visible_span
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            label = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
            self.create_line(x, ruler_y - 3, x, ruler_y + 3, fill=BORDER)
            self.create_text(x, ruler_y + 15, text=label, fill=MUTED, font=("Segoe UI", 8))

        if self.tool == TOOL_BLADE and self._hover_x is not None:
            blade_time = self.snapped_time(self._hover_x)
            if self.view_start <= blade_time <= self.view_end:
                blade_x = self._time_to_x(blade_time)
                self.create_line(blade_x, strip_top - 8, blade_x, strip_bottom + 8, fill=BLADE, width=2, dash=(4, 2))
                self.create_text(blade_x + 3, strip_top - 1, text="✂", fill=BLADE, anchor="sw",
                                 font=("Segoe UI Symbol", 11, "bold"))

        zoomed = visible_span < self.duration - 0.001
        if self.tool == TOOL_BLADE:
            hint = "Blade (B): click to cut • V = select tool • Ctrl+K cuts at the playhead"
        elif self.edit is not None and self.edit.cuts:
            hint = "Click a part to select • Delete removes it • drag a yellow cut to move it • right-click for more"
        elif zoomed:
            hint = "Zoomed timeline • drag the playhead to scrub"
        else:
            hint = "Drag the blue handles to trim • B = blade tool to cut • Space = play/pause"
        self.create_text(
            width / 2,
            strip_bottom + 9,
            text=hint,
            fill=MUTED,
            font=("Segoe UI", 8),
            anchor="n",
        )

    def _draw_segments(self, strip_top: float, strip_bottom: float) -> None:
        edit = self.edit
        if edit is None:
            return
        for segment in edit.segments():
            a = max(segment.start, self.view_start)
            b = min(segment.end, self.view_end)
            if b <= a:
                continue
            x1 = self._time_to_x(a)
            x2 = self._time_to_x(b)
            middle = (x1 + x2) / 2
            if segment.state == REMOVED:
                self.create_rectangle(x1, strip_top, x2, strip_bottom, fill=REMOVED_FILL, stipple="gray75",
                                      outline="")
                self.create_line(x1, strip_top, x2, strip_bottom, fill=BLADE, width=1)
                self.create_line(x1, strip_bottom, x2, strip_top, fill=BLADE, width=1)
                if x2 - x1 > 54:
                    self.create_text(middle, (strip_top + strip_bottom) / 2, text="Removed", fill="#FFFFFF",
                                     font=("Segoe UI", 8, "bold"))
            elif segment.state == GAP:
                self.create_rectangle(x1, strip_top, x2, strip_bottom, fill=GAP_FILL, outline="")
                if x2 - x1 > 40:
                    self.create_text(middle, (strip_top + strip_bottom) / 2, text="Gap", fill="#C0C0C0",
                                     font=("Segoe UI", 8, "bold"))
            if segment.index in self.selected:
                self.create_rectangle(x1 + 1, strip_top + 1, x2 - 1, strip_bottom - 1, outline=SELECTED, width=3)
        for cut in edit.cuts:
            if not self.view_start <= cut <= self.view_end:
                continue
            x = self._time_to_x(cut)
            self.create_line(x, strip_top, x, strip_bottom, fill=CUT_LINE, width=2)
            self.create_polygon(x - 4, strip_bottom, x + 4, strip_bottom, x, strip_bottom - 6,
                                fill=CUT_LINE, outline=CUT_LINE)

    def _closest_target(self, x: float) -> str:
        distances: dict[str, float] = {}
        if self.view_start <= self.start <= self.view_end:
            distances["start"] = abs(x - self._time_to_x(self.start))
        if self.view_start <= self.end <= self.view_end:
            distances["end"] = abs(x - self._time_to_x(self.end))
        if self.view_start <= self.playhead <= self.view_end:
            distances["playhead"] = abs(x - self._time_to_x(self.playhead))
        if distances:
            target, distance = min(distances.items(), key=lambda item: item[1])
            if distance <= 14:
                return target
        return "playhead"

    @staticmethod
    def _in_strip(y: float) -> bool:
        return 8 <= y <= 76

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

        target = self._closest_target(event.x)
        if target == "playhead" and in_strip:
            cut = self._cut_near(event.x)
            if cut is not None:
                self.drag_target = "cut"
                self._drag_cut = cut
                self._drag_cut_origin = self.edit.cuts[cut] if self.edit else None
                return "break"

        if in_strip and self.edit is not None and target == "playhead":
            index = self.edit.index_at(self._x_to_time(event.x))
            if toggle:
                selection = set(self.selected)
                selection.symmetric_difference_update({index})
                self._anchor = index
            elif extend and self._anchor is not None:
                low, high = sorted((self._anchor, index))
                selection = set(range(low, high + 1))
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
        index = self.edit.index_at(self._x_to_time(event.x))
        if index not in self.selected:
            self._anchor = index
            self.set_selection({index})
        self.on_context_menu(event, index)
        return "break"

    def _on_wheel(self, event) -> str:
        span = self.view_end - self.view_start
        if span >= self.duration - 0.001:
            return "break"
        shift = -span * 0.1 if event.delta > 0 else span * 0.1
        start = max(0.0, min(self.view_start + shift, self.duration - span))
        self.view_start, self.view_end = start, start + span
        self.redraw()
        return "break"

    def _on_ctrl_wheel(self, event) -> str:
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        return "break"

    def _apply_drag(self, x: float, *, notify: bool) -> None:
        value = self._x_to_time(x)
        minimum_gap = min(0.1, self.duration / 100)

        if self.drag_target in {"start", "end"} and self.snap_enabled:
            value = snap(value, [cut for cut in (self.edit.cuts if self.edit else [])] + [self.playhead],
                         SNAP_PX * self._seconds_per_px())

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
