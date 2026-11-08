"""In-place updating download list drawn on a page canvas.

Rows are created once per download and only the properties that changed are
touched, so progress bars glide smoothly and nothing is rebuilt on every refresh.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

import ui_kit as ui
from ui_kit import px

STATUS_STYLE = {
    "queued": ("QUEUED", ui.MUTED, ui.SURFACE_3),
    "running": ("DOWNLOADING", ui.CYAN, ui.INFO_SOFT),
    "cancelling": ("CANCELLING", ui.WARNING, ui.WARNING_SOFT),
    "completed": ("DONE", ui.SUCCESS, ui.SUCCESS_SOFT),
    "failed": ("FAILED", ui.DANGER, ui.DANGER_SOFT),
    "cancelled": ("CANCELLED", ui.WARNING, ui.WARNING_SOFT),
    "paused": ("PAUSED", ui.WARNING, ui.WARNING_SOFT),
}
BAR_COLORS = {
    "completed": ui.SUCCESS, "failed": ui.DANGER, "cancelled": ui.WARNING, "cancelling": ui.WARNING,
    "paused": ui.WARNING,
}
ROW_H = 78


def reveal_in_folder(path: str) -> None:
    target = Path(path)
    try:
        if os.name == "nt":
            if target.exists():
                subprocess.Popen(["explorer", f"/select,{target}"])
            else:
                os.startfile(str(target.parent))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
    except Exception:
        pass


class DownloadRow:
    def __init__(self, page: ui.Page, *, on_cancel: Callable[[str], None],
                 on_retry: Callable[[str], None],
                 on_pause: Callable[[str], None] | None = None,
                 on_resume: Callable[[str], None] | None = None) -> None:
        self.page = page
        self.request_id = ""
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._pause_mode = ""
        self._state: dict[str, Any] = {}
        self._mode = ""
        self._path = ""
        self._numbered = False

        self.bg = ui.Card(page, fill=ui.SURFACE_2, outline=ui.BORDER_SOFT, radius=10)
        self.index = ui.Label(page, "", fg=ui.FAINT, size=11, bold=True, relayout=False)
        self.title = ui.Label(page, "", fg=ui.TEXT, size=12, bold=True, ellipsis=True, relayout=False)
        self.chip = ui.Chip(page, "QUEUED", fg=ui.MUTED, bg=ui.SURFACE_3, size=9)
        self.bar = ui.Bar(page, height=6, color=ui.ACCENT, track=ui.SURFACE_3)
        self.detail = ui.Label(page, "", fg=ui.MUTED, size=10, ellipsis=True, relayout=False)
        self.action = ui.Btn(page, "Cancel", self._act, style="danger", size=11, padx=14, radius=8)
        self.show_btn = ui.Btn(page, "Show in folder", self._show, style="ghost", size=11, padx=14, radius=8)
        self.pause_btn = ui.Btn(page, "Pause", self._pause_click, style="secondary", size=11, padx=14, radius=8)
        self.action.show(False)
        self.show_btn.show(False)
        self.pause_btn.show(False)
        self.parts = [self.bg, self.index, self.title, self.chip, self.bar, self.detail,
                      self.action, self.show_btn, self.pause_btn]

    def destroy(self) -> None:
        for part in self.parts:
            part.destroy()

    def _act(self) -> None:
        if self._mode == "cancel":
            self._on_cancel(self.request_id)
        elif self._mode == "retry":
            self._on_retry(self.request_id)

    def _pause_click(self) -> None:
        if self._pause_mode == "pause" and self._on_pause:
            self._on_pause(self.request_id)
        elif self._pause_mode == "resume" and self._on_resume:
            self._on_resume(self.request_id)

    def _show(self) -> None:
        if self._path:
            reveal_in_folder(self._path)

    def apply(self, item: dict[str, Any], number: int | None) -> None:
        status = str(item.get("status") or "queued")
        label, fg, bg = STATUS_STYLE.get(status, (status.upper(), ui.MUTED, ui.SURFACE_3))
        title = str(item.get("name") or "Media download")
        detail = str(item.get("error") or "") if status == "failed" else str(item.get("detail") or "")
        if not detail:
            if status == "queued":
                position = int(item.get("position") or 0)
                detail = f"Waiting in queue • position {position}" if position else "Waiting"
            elif status == "completed":
                detail = "Download completed"
            elif status == "cancelled":
                detail = "Download cancelled"
            else:
                detail = "Starting…"
        progress = 1.0 if status == "completed" else max(0.0, min(1.0, float(item.get("progress") or 0.0)))

        if self._state.get("title") != title:
            self._state["title"] = title
            self.title.configure(text=title[:160])
        if self._state.get("number") != number:
            self._state["number"] = number
            self._numbered = number is not None
            self.index.configure(text=f"{number:02d}" if number else "")
            self.bg.relayout()
        if self._state.get("status") != status:
            self._state["status"] = status
            self.chip.configure(text=label, fg=fg, bg=bg)
            self.bar.set_color(BAR_COLORS.get(status, ui.ACCENT))
            self.detail.configure(fg=ui.DANGER if status == "failed" else ui.MUTED)
        if self._state.get("detail") != detail:
            self._state["detail"] = detail
            self.detail.configure(text=detail[:220])

        first = not self._state.get("seen")
        self._state["seen"] = True
        if status == "running" and progress <= 0.001:
            self.bar.set_indeterminate(True)
        else:
            self.bar.set(0.0 if status == "queued" else progress, animate=not first)

        path = str(item.get("result_path") or "")
        mode = "cancel" if status in {"queued", "running", "paused"} else "retry" if status in {"failed", "cancelled"} \
            else "busy" if status == "cancelling" else ""
        pause_mode = ""
        if self._on_pause and status in {"queued", "running"}:
            pause_mode = "pause"
        elif self._on_resume and status == "paused":
            pause_mode = "resume"
        sig = (mode, status == "completed" and bool(path), pause_mode)
        self._path = path
        if self._state.get("actions") != sig:
            self._state["actions"] = sig
            self._mode = mode
            if mode == "cancel":
                self.action.configure(text="Cancel", style="danger", state="normal")
            elif mode == "retry":
                self.action.configure(text="Retry", style="primary", state="normal")
            elif mode == "busy":
                self.action.configure(text="Cancelling…", style="danger", state="disabled")
            self.action.show(mode != "")
            self.show_btn.show(sig[1])
            self._pause_mode = pause_mode
            if pause_mode:
                self.pause_btn.configure(text="Pause" if pause_mode == "pause" else "Resume",
                                         style="secondary" if pause_mode == "pause" else "primary")
            self.pause_btn.show(bool(pause_mode))
            self.bg.relayout()

    def place(self, x: float, y: float, w: float) -> None:
        h = px(ROW_H)
        self.bg.place(x, y, w, h)
        pad = px(16)
        right = x + w - pad
        chip_w = self.chip.natural_w()
        self.chip.place(right - chip_w, y + px(12))
        left = x + pad
        if self._numbered:
            self.index.place(left, y + px(14))
            left += px(30)
        self.title.place(left, y + px(13), max(20, right - chip_w - px(12) - left))

        edge = right
        for button in (self.action, self.show_btn, self.pause_btn):
            if button.visible:
                width = button.natural_w(minimum=82)
                button.place(edge - width, y + px(40), width, px(28))
                edge -= width + px(8)
        bar_right = edge - px(4) if edge < right else right
        bar_w = max(20, bar_right - (x + pad))
        self.bar.place(x + pad, y + px(47), bar_w)
        self.detail.place(x + pad, y + px(56), bar_w)


class ActivityList:
    """Keeps one ``DownloadRow`` per request and reuses it between refreshes."""

    def __init__(self, page: ui.Page, *, on_cancel: Callable[[str], None], on_retry: Callable[[str], None],
                 empty_text: str, numbered: bool = False,
                 on_pause: Callable[[str], None] | None = None,
                 on_resume: Callable[[str], None] | None = None) -> None:
        self.page = page
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._numbered = numbered
        self.rows: dict[str, DownloadRow] = {}
        self.order: list[str] = []
        self.empty = ui.Label(page, empty_text, fg=ui.FAINT, size=12, anchor="n", wrap=True)

    def sync(self, items: Iterable[dict[str, Any]], limit: int | None = None) -> None:
        items = list(items)
        if limit is not None:
            items = items[:limit]
        wanted = [str(item.get("id") or "") for item in items]
        wanted_set = set(wanted)
        changed = False
        for request_id in [rid for rid in self.rows if rid not in wanted_set]:
            self.rows.pop(request_id).destroy()
            changed = True
        for position, item in enumerate(items):
            request_id = wanted[position]
            row = self.rows.get(request_id)
            if row is None:
                row = DownloadRow(self.page, on_cancel=self._on_cancel, on_retry=self._on_retry,
                                  on_pause=self._on_pause, on_resume=self._on_resume)
                row.request_id = request_id
                self.rows[request_id] = row
                changed = True
            row.apply(item, number=(position + 1) if self._numbered else None)
        if wanted != self.order:
            self.order = wanted
            changed = True
        self.empty.show(not items)
        if changed:
            self.page.request_layout()

    def place(self, x: float, y: float, w: float) -> float:
        if not self.order:
            self.empty.place(x + w / 2, y + px(10), w - px(40))
            return float(px(54))
        gap = px(8)
        cursor = y
        for request_id in self.order:
            self.rows[request_id].place(x, cursor, w)
            cursor += px(ROW_H) + gap
        return cursor - y - gap
