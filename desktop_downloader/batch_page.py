"""The "Add batch download" dialog: paste many links (or load a .txt) and queue them.

Progress of a batch is shown in the main download list under Queues.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

import ui_kit as ui
from batch_download import MAX_BATCH_URLS, parse_batch_urls_detailed
from ui_kit import px

VIDEO_QUALITIES = ["Best available", "1080p", "720p", "480p", "360p"]


class BatchSection:
    def __init__(
        self,
        page: ui.Page,
        *,
        on_enqueue: Callable[[str, dict[str, str]], tuple[str, int, int]],
        settings: dict[str, Any],
        on_queued: Callable[[str], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.page = page
        self._on_enqueue = on_enqueue
        self._on_queued = on_queued

        self.mode_var = tk.StringVar(value=str(settings.get("default_mode") or "Video"))
        self.quality_var = tk.StringVar(value=str(settings.get("video_quality") or "720p"))
        self.audio_format_var = tk.StringVar(value=str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var = tk.StringVar(value=str(settings.get("audio_quality") or "192"))

        # input card
        self.input_card = ui.Card(page)
        self.cap_links = ui.Label(page, "ADD LINKS", fg=ui.CYAN, size=10, bold=True)
        self.count_label = ui.Label(page, f"0 / {MAX_BATCH_URLS} links", fg=ui.MUTED, size=11, anchor="ne",
                                    relayout=False)
        self.url_area = ui.TextArea(page)
        self.url_area.text.bind("<KeyRelease>", self._update_count)
        self.url_area.text.bind("<<Paste>>", lambda _e: page.after(40, self._after_paste), add="+")

        self.cap_type = ui.Label(page, "OUTPUT TYPE", fg=ui.MUTED, size=9, bold=True)
        self.cap_quality = ui.Label(page, "VIDEO QUALITY", fg=ui.MUTED, size=9, bold=True)
        self.cap_format = ui.Label(page, "AUDIO FORMAT", fg=ui.MUTED, size=9, bold=True)
        self.cap_bitrate = ui.Label(page, "BITRATE (KBPS)", fg=ui.MUTED, size=9, bold=True)
        self.mode_control = ui.Segmented(page, ["Video", "Audio"], self.mode_var, command=lambda _v: self._sync_mode())
        self.quality_select = ui.Select(page, VIDEO_QUALITIES, self.quality_var)
        self.format_select = ui.Select(page, ["MP3", "M4A", "FLAC", "OPUS", "WAV"], self.audio_format_var)
        self.bitrate_select = ui.Select(page, ["128", "192", "256", "320"], self.audio_quality_var)

        self.start_button = ui.Btn(page, "Add to queue", self._enqueue, style="primary", size=12)
        self.paste_button = ui.Btn(page, "Paste", self._paste, style="secondary", size=12)
        self.file_button = ui.Btn(page, "Load .txt", self._load_file, style="secondary", size=12)
        self.clear_button = ui.Btn(page, "Clear", self._clear, style="ghost", size=12)
        self.status_label = ui.Label(page, "Paste one link per line, or load a .txt file — up to 300 at a time.", fg=ui.MUTED, size=11,
                                     anchor="ne", ellipsis=True, relayout=False)

        self.close_button = ui.Btn(page, "Close", on_close or (lambda: None), style="ghost", size=12)
        self._sync_mode()

    # layout ------------------------------------------------------------------
    def layout(self, x0: float, y: float, cw: float, h: float = 0) -> float:
        pad = px(22)
        gap = px(12)
        inner_x = x0 + pad
        inner_w = cw - 2 * pad

        top = y
        area_h = max(px(150), int(h - y - px(250))) if h else px(190)
        opts_y = top + px(52) + area_h + px(18)
        card_h = (opts_y + px(66) + px(22)) - top
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

        action_y = top + card_h + px(16)
        bx = x0
        for button in (self.paste_button, self.file_button, self.clear_button):
            bw = button.natural_w(minimum=84)
            button.place(bx, action_y, bw, px(42))
            bx += bw + px(8)
        rx = x0 + cw
        for button in (self.start_button, self.close_button):
            bw = button.natural_w(minimum=96)
            button.place(rx - bw, action_y, bw, px(42))
            rx -= bw + px(8)
        self.status_label.place(rx - px(8), action_y + px(12), max(40, rx - px(16) - bx))
        return action_y + px(42)

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

    def _after_paste(self) -> None:
        """Move the cursor to a fresh line after a pasted link so the next paste lands on its own line."""
        text = self.url_area.text
        try:
            line_start = text.index("insert linestart")
            before_cursor = text.get(line_start, "insert")
            after_cursor = text.get("insert", "insert lineend")
            if before_cursor.strip() and not after_cursor.strip():
                text.insert("insert", "\n")
                text.mark_set("insert", "insert lineend")
                text.see("insert")
        except tk.TclError:
            pass
        self._update_count()

    def _paste(self) -> None:
        try:
            value = self.page.clipboard_get()
        except Exception:
            return
        text = self.url_area.text
        existing = self._text()
        if existing and not existing.endswith("\n"):
            text.insert("end-1c", "\n")
        text.insert("end-1c", value.strip() + "\n")
        text.see("end")
        self._update_count()

    def _load_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Load links from a text file",
            filetypes=[("Text files", "*.txt *.csv *.list *.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                content = handle.read(2_000_000)
        except OSError as exc:
            messagebox.showerror("Batch Download", f"Could not read the file. {exc}")
            return
        text = self.url_area.text
        existing = self._text()
        if existing and not existing.endswith("\n"):
            text.insert("end-1c", "\n")
        text.insert("end-1c", content.strip() + "\n")
        text.see("end")
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
        if self._on_queued:
            self._on_queued(batch_id)

    def refresh(self, snapshots: list[dict[str, Any]] | None = None) -> None:
        """Kept for compatibility: batch progress now lives in the main list (Queues)."""

    def focus(self) -> None:
        self.url_area.text.focus_set()
