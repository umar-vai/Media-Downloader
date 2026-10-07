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
        self.on_seek = on_seek
        self.on_range_change = on_range_change
        self.drag_target: str | None = None
        self.thumbnail_pils: list[Image.Image] = []
        self.thumbnail_refs: list[ImageTk.PhotoImage] = []
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
        self.redraw()
        if notify and self.on_seek:
            self.on_seek(self.playhead)

    def set_thumbnails(self, images: list[Image.Image]) -> None:
        self.thumbnail_pils = [image.copy() for image in images]
        self.redraw()

    def _x_bounds(self) -> tuple[float, float]:
        width = max(10, self.winfo_width())
        return 18.0, float(width - 18)

    def _time_to_x(self, value: float) -> float:
        left, right = self._x_bounds()
        return left + (max(0.0, min(value, self.duration)) / self.duration) * (right - left)

    def _x_to_time(self, x: float) -> float:
        left, right = self._x_bounds()
        if right <= left:
            return 0.0
        fraction = (max(left, min(float(x), right)) - left) / (right - left)
        return fraction * self.duration

    def redraw(self) -> None:
        self.delete("all")
        width = max(20, self.winfo_width())
        left, right = self._x_bounds()
        strip_top = 10
        strip_bottom = 74
        ruler_y = 94

        self.create_rectangle(left, strip_top, right, strip_bottom, fill=SURFACE_2, outline=BORDER)

        self.thumbnail_refs = []
        if self.thumbnail_pils:
            count = len(self.thumbnail_pils)
            available = max(1, right - left)
            tile_w = max(20, int(available / count))
            tile_h = strip_bottom - strip_top
            for index, source in enumerate(self.thumbnail_pils):
                image = source.copy()
                image.thumbnail((tile_w, tile_h))
                canvas_image = ImageTk.PhotoImage(image)
                self.thumbnail_refs.append(canvas_image)
                x = left + index * tile_w
                self.create_image(x, strip_top, anchor="nw", image=canvas_image)

        start_x = self._time_to_x(self.start)
        end_x = self._time_to_x(self.end)
        play_x = self._time_to_x(self.playhead)

        self.create_rectangle(left, strip_top, start_x, strip_bottom, fill="#020712", stipple="gray50", outline="")
        self.create_rectangle(end_x, strip_top, right, strip_bottom, fill="#020712", stipple="gray50", outline="")
        self.create_rectangle(start_x, strip_top, end_x, strip_bottom, outline=CYAN, width=2)

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
            seconds = fraction * self.duration
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            label = f"{minutes}:{secs:02d}"
            self.create_line(x, ruler_y - 3, x, ruler_y + 3, fill=BORDER)
            self.create_text(x, ruler_y + 15, text=label, fill=MUTED, font=("Segoe UI", 8))

        self.create_text(
            width / 2,
            strip_bottom + 9,
            text="Drag purple handles to trim • Drag cyan playhead to scrub",
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
