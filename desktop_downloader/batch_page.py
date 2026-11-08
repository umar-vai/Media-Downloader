"""Batch downloads, drawn inside the main window (no separate popup)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

import ui_kit as ui
from activity_view import ActivityList
from batch_download import MAX_BATCH_URLS, batch_summaries, parse_batch_urls_detailed
from ui_kit import px

VIDEO_QUALITIES = ["Best available", "1080p", "720p", "480p", "360p"]


class BatchSection:
    def __init__(
        self,
        page: ui.Page,
        *,
        on_enqueue: Callable[[str, dict[str, str]], tuple[str, int, int]],
        on_cancel: Callable[[str], None],
        on_retry: Callable[[str], None],
        on_pause: Callable[[str], None] | None = None,
        on_resume: Callable[[str], None] | None = None,
        get_snapshots: Callable[[], list[dict[str, Any]]],
        settings: dict[str, Any],
    ) -> None:
        self.page = page
        self._on_enqueue = on_enqueue
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._get_snapshots = get_snapshots
        self._group_ids: dict[str, str] = {}
        self._selected_group = ""

        self.mode_var = tk.StringVar(value=str(settings.get("default_mode") or "Video"))
        self.quality_var = tk.StringVar(value=str(settings.get("video_quality") or "720p"))
        self.audio_format_var = tk.StringVar(value=str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var = tk.StringVar(value=str(settings.get("audio_quality") or "192"))
        self.batch_choice_var = tk.StringVar(value="No batches yet")

        # input card
        self.input_card = ui.Card(page)
        self.cap_links = ui.Label(page, "ADD LINKS", fg=ui.CYAN, size=10, bold=True)
        self.count_label = ui.Label(page, f"0 / {MAX_BATCH_URLS} links", fg=ui.MUTED, size=11, anchor="ne",
                                    relayout=False)
        self.url_area = ui.TextArea(page)
        self.url_area.text.bind("<KeyRelease>", self._update_count)
        self.url_area.text.bind("<<Paste>>", lambda _e: page.after(40, self._update_count), add="+")

        self.cap_type = ui.Label(page, "OUTPUT TYPE", fg=ui.MUTED, size=9, bold=True)
        self.cap_quality = ui.Label(page, "VIDEO QUALITY", fg=ui.MUTED, size=9, bold=True)
        self.cap_format = ui.Label(page, "AUDIO FORMAT", fg=ui.MUTED, size=9, bold=True)
        self.cap_bitrate = ui.Label(page, "BITRATE (KBPS)", fg=ui.MUTED, size=9, bold=True)
        self.mode_control = ui.Segmented(page, ["Video", "Audio"], self.mode_var, command=lambda _v: self._sync_mode())
        self.quality_select = ui.Select(page, VIDEO_QUALITIES, self.quality_var)
        self.format_select = ui.Select(page, ["MP3", "M4A"], self.audio_format_var)
        self.bitrate_select = ui.Select(page, ["128", "192", "256", "320"], self.audio_quality_var)

        self.start_button = ui.Btn(page, "Add batch to queue", self._enqueue, style="primary", size=12)
        self.paste_button = ui.Btn(page, "Paste", self._paste, style="secondary", size=12)
        self.clear_button = ui.Btn(page, "Clear", self._clear, style="ghost", size=12)
        self.status_label = ui.Label(page, "Paste one link per line — up to 50 at a time.", fg=ui.MUTED, size=11,
                                     anchor="ne", ellipsis=True, relayout=False)

        # progress card
        self.progress_card = ui.Card(page)
        self.cap_progress = ui.Label(page, "BATCH PROGRESS", fg=ui.CYAN, size=10, bold=True)
        self.group_choice = ui.Select(page, ["No batches yet"], self.batch_choice_var, command=self._choose_batch,
                                      size=11)
        self.cancel_all_button = ui.Btn(page, "Cancel remaining", self._cancel_group, style="danger", size=11,
                                        state="disabled")
        self.retry_all_button = ui.Btn(page, "Retry failed", self._retry_group, style="secondary", size=11,
                                       state="disabled")
        self.summary_label = ui.Label(page, "No batches submitted yet", fg=ui.MUTED, size=11, relayout=False,
                                      ellipsis=True)
        self.percent_label = ui.Label(page, "", fg=ui.TEXT, size=11, bold=True, anchor="ne", relayout=False)
        self.overall_bar = ui.Bar(page, height=8, color=ui.ACCENT)
        self.items = ActivityList(page, on_cancel=on_cancel, on_retry=on_retry, on_pause=on_pause, on_resume=on_resume, numbered=True,
                                  empty_text="Links you add will appear here with live progress.")
        self._sync_mode()

    # layout ------------------------------------------------------------------
    def layout(self, x0: float, y: float, cw: float) -> float:
        pad = px(22)
        gap = px(12)
        inner_x = x0 + pad
        inner_w = cw - 2 * pad

        # input card
        top = y
        area_h = px(150)
        opts_y = top + px(52) + area_h + px(18)
        action_y = opts_y + px(84)
        card_h = (action_y + px(48) + px(22)) - top
        self.input_card.place(x0, top, cw, card_h)
        self.cap_links.place(inner_x, top + px(20))
        self.count_label.place(x0 + cw - pad, top + px(20))
        self.url_area.place(inner_x, top + px(48), inner_w, area_h)

        col_w = (inner_w - 3 * gap) / 4
        cells = [
            (self.cap_type, self.mode_control), (self.cap_quality, self.quality_select),
            (self.cap_format, self.format_select), (self.cap_bitrate, self.bitrate_select),
        ]
        for index, (cap, control) in enumerate(cells):
            cx = inner_x + index * (col_w + gap)
            cap.place(cx, opts_y)
            control.place(cx, opts_y + px(22), col_w, px(44))

        bx = inner_x
        self.start_button.place(bx, action_y, self.start_button.natural_w(), px(46))
        bx += self.start_button.natural_w() + px(8)
        self.paste_button.place(bx, action_y, self.paste_button.natural_w(), px(46))
        bx += self.paste_button.natural_w() + px(8)
        self.clear_button.place(bx, action_y, self.clear_button.natural_w(), px(46))
        bx += self.clear_button.natural_w() + px(16)
        self.status_label.place(x0 + cw - pad, action_y + px(14), max(40, x0 + cw - pad - bx))

        # progress card
        top = y + card_h + px(16)
        head_y = top + px(14)
        self.cap_progress.place(inner_x, top + px(22))
        rx = x0 + cw - pad
        rw = self.retry_all_button.natural_w()
        self.retry_all_button.place(rx - rw, head_y, rw, px(36))
        rx -= rw + px(8)
        cw2 = self.cancel_all_button.natural_w()
        self.cancel_all_button.place(rx - cw2, head_y, cw2, px(36))
        rx -= cw2 + px(10)
        sel_w = min(px(240), max(px(150), rx - inner_x - px(160)))
        self.group_choice.place(rx - sel_w, head_y, sel_w, px(36))

        self.summary_label.place(inner_x, top + px(66), inner_w - px(70))
        self.percent_label.place(x0 + cw - pad, top + px(66))
        self.overall_bar.place(inner_x, top + px(92), inner_w)
        list_h = self.items.place(inner_x, top + px(116), inner_w)
        prog_h = px(116) + list_h + px(22)
        self.progress_card.place(x0, top, cw, prog_h)
        return top + prog_h

    # form helpers ----------------------------------------------------------------
    def _sync_mode(self) -> None:
        audio = self.mode_var.get() == "Audio"
        self.quality_select.configure(state="disabled" if audio else "normal")
        self.format_select.configure(state="normal" if audio else "disabled")
        self.bitrate_select.configure(state="normal" if audio else "disabled")

    def set_defaults(self, settings: dict[str, Any]) -> None:
        self.mode_var.set(str(settings.get("default_mode") or "Video"))
        self.quality_var.set(str(settings.get("video_quality") or "720p"))
        self.audio_format_var.set(str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var.set(str(settings.get("audio_quality") or "192"))
        self._sync_mode()

    def _text(self) -> str:
        return self.url_area.text.get("1.0", "end-1c")

    def _update_count(self, _event: Any = None) -> None:
        count, dupes, bad = 0, 0, 0
        try:
            urls, dupes, bad = parse_batch_urls_detailed(self._text())[0:3]
            count = len(urls)
        except ValueError as exc:
            if "up to" in str(exc):
                count = MAX_BATCH_URLS + 1
        extra = f" • {dupes} duplicate" if dupes else ""
        extra += f" • {bad} invalid" if bad else ""
        self.count_label.configure(text=f"{count} / {MAX_BATCH_URLS} links{extra}",
                                   text_color=ui.DANGER if count > MAX_BATCH_URLS else ui.MUTED)

    def _paste(self) -> None:
        try:
            value = self.page.clipboard_get()
        except Exception:
            return
        existing = self._text().strip()
        self.url_area.text.insert("end", ("\n" if existing else "") + value.strip())
        self._update_count()

    def _clear(self) -> None:
        self.url_area.text.delete("1.0", "end")
        self._update_count()

    def _enqueue(self) -> None:
        try:
            urls, duplicates, invalid = parse_batch_urls_detailed(self._text())
        except ValueError as exc:
            messagebox.showerror("Batch Download", str(exc))
            return
        try:
            batch_id, queued, _dupes = self._on_enqueue(
                "\n".join(urls),
                {
                    "mode": self.mode_var.get(),
                    "video_quality": self.quality_var.get(),
                    "audio_format": self.audio_format_var.get(),
                    "audio_quality": self.audio_quality_var.get(),
                },
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("Batch Download", str(exc))
            return
        note = f"Queued {queued} link{'s' if queued != 1 else ''}"
        if duplicates:
            note += f" • {duplicates} duplicate skipped"
        if invalid:
            note += f" • {invalid} invalid skipped"
        self.status_label.configure(text=note, text_color=ui.SUCCESS)
        self._clear()
        self._selected_group = batch_id
        self.refresh()

    # batch actions ------------------------------------------------------------------
    def _choose_batch(self, choice: str) -> None:
        self._selected_group = self._group_ids.get(choice, "")
        self.refresh()

    def _cancel_group(self) -> None:
        for item in self._get_snapshots():
            if item.get("batch_id") == self._selected_group and item.get("status") in {"queued", "running", "paused"}:
                self._on_cancel(str(item["id"]))
        self.refresh()

    def _retry_group(self) -> None:
        for item in self._get_snapshots():
            if item.get("batch_id") == self._selected_group and item.get("status") == "failed":
                self._on_retry(str(item["id"]))
        self.refresh()

    # live view ----------------------------------------------------------------------
    def refresh(self, snapshots: list[dict[str, Any]] | None = None) -> None:
        snapshots = self._get_snapshots() if snapshots is None else snapshots
        batches = batch_summaries(snapshots)
        choices = [f"Batch {len(batches) - i} • {b['total']} links" for i, b in enumerate(batches)]
        mapping = {label: summary["batch_id"] for label, summary in zip(choices, batches)}
        if mapping != self._group_ids:
            self._group_ids = mapping
            self.group_choice.configure(values=choices or ["No batches yet"])

        selected = next((b for b in batches if b["batch_id"] == self._selected_group), None)
        if selected is None and batches:
            selected = batches[0]
            self._selected_group = selected["batch_id"]
        if selected:
            label = next(lbl for lbl, bid in mapping.items() if bid == self._selected_group)
            if self.batch_choice_var.get() != label:
                self.batch_choice_var.set(label)
        elif self.batch_choice_var.get() != "No batches yet":
            self.batch_choice_var.set("No batches yet")

        self.cancel_all_button.configure(state="normal" if selected and selected["active"] else "disabled")
        self.retry_all_button.configure(state="normal" if selected and selected["failed"] else "disabled")
        if selected:
            running = sum(1 for item in selected["items"] if item.get("status") == "running")
            parts = [f"{selected['completed']} done"]
            if running:
                parts.append(f"{running} downloading")
            waiting = selected["active"] - running
            if waiting > 0:
                parts.append(f"{waiting} waiting")
            if selected["failed"]:
                parts.append(f"{selected['failed']} failed")
            if selected["cancelled"]:
                parts.append(f"{selected['cancelled']} cancelled")
            self.summary_label.configure(text="  •  ".join(parts))
            self.overall_bar.set(float(selected["progress"]))
            self.percent_label.configure(text=f"{int(selected['progress'] * 100)}%")
            self.items.sync(selected["items"])
        else:
            self.summary_label.configure(text="No batches submitted yet")
            self.overall_bar.set(0.0)
            self.percent_label.configure(text="")
            self.items.sync([])
