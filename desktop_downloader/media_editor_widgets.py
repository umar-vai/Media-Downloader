from __future__ import annotations

import tkinter as tk
import time
from typing import Callable

from PIL import Image, ImageTk

BG = "#07101D"
SURFACE_2 = "#101C31"
SURFACE_3 = "#14223A"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
CYAN = "#23D5FF"
PURPLE = "#7657FF"


class TimelineCanvas(tk.Canvas):
    def __init__(
        self,
        master,
        *,
        on_seek: Callable[[float], None] | None = None,
        on_range_change: Callable[[float, float], None] | None = None,
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

        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

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
            self.create_rectangle(start_x, strip_top, end_x, strip_bottom, outline=CYAN, width=2)
        else:
            self.create_rectangle(left, strip_top, right, strip_bottom, fill="#020712", stipple="gray50", outline="")
            start_x = self._time_to_x(self.start)
            end_x = self._time_to_x(self.end)

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

        zoomed = visible_span < self.duration - 0.001
        hint = "Zoomed timeline • drag cyan playhead to scrub" if zoomed else "Drag purple handles to trim • Drag cyan playhead to scrub"
        self.create_text(
            width / 2,
            strip_bottom + 9,
            text=hint,
            fill=MUTED,
            font=("Segoe UI", 8),
            anchor="n",
        )

    def _closest_target(self, x: float) -> str:
        start_x = self._time_to_x(self.start)
        end_x = self._time_to_x(self.end)
        play_x = self._time_to_x(self.playhead)
        distances = {
            "start": abs(x - start_x),
            "end": abs(x - end_x),
            "playhead": abs(x - play_x),
        }
        target, distance = min(distances.items(), key=lambda item: item[1])
        if distance <= 14:
            return target
        return "playhead"

    def _on_press(self, event) -> None:
        self.drag_target = self._closest_target(event.x)
        self._apply_drag(event.x, notify=False)

    def _on_drag(self, event) -> None:
        notify = False
        if self.drag_target == "playhead":
            now = time.monotonic()
            if now - self._last_scrub_notify >= 0.04:
                self._last_scrub_notify = now
                notify = True
        self._apply_drag(event.x, notify=notify)

    def _on_release(self, event) -> None:
        self._apply_drag(event.x, notify=True)
        self.drag_target = None

    def _apply_drag(self, x: float, *, notify: bool) -> None:
        value = self._x_to_time(x)
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
