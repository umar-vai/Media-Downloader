"""Main-window layout for Media Downloader: sidebar + single-window pages.

Download, Batch, Queue and Updates all live in one window. Each page is one canvas
(see ``ui_kit``) so dragging the window edge stays smooth, and switching pages just
shows/hides a canvas.
"""

from __future__ import annotations

import io
import tkinter as tk
import webbrowser

from PIL import Image, ImageTk

import ui_kit as ui
from activity_view import ActivityList
from batch_page import BatchSection
from ui_kit import px
from version import APP_VERSION

NAV = [
    ("download", "Download", ""),
    ("batch", "Batch", ""),
    ("queue", "Queue", ""),
    ("updates", "Updates", ""),
]
TOOLS = [("Media editor", "", "open_editor"), ("History", "", "open_history"),
         ("Settings", "", "open_settings")]

TITLES = {
    "download": ("Download", "Paste a link, pick a format and save it."),
    "batch": ("Batch download", "Add many links at once — everything stays right here."),
    "queue": ("Queue", "Every download, with live progress."),
    "updates": ("Updates & about", "Keep Media Downloader current."),
}
MAX_CONTENT = 1060
THUMB_W, THUMB_H = 232, 130


class UIMixin:
    """Builds and refreshes the visible UI. Mixed into ``DownloaderApp``."""

    # ----------------------------------------------------------------- build
    def _build_ui(self) -> None:
        ui.init(self)
        ui.install_wheel(self)
        self.current_page = "download"
        self._icons = ui.icon_available(self)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.side = ui.Page(self, self._layout_sidebar, bg=ui.SIDEBAR, scroll=False)
        self.side.configure(width=px(236))
        self.side.grid(row=0, column=0, sticky="ns")
        self.side.grid_propagate(False)

        self.stage = tk.Frame(self, bg=ui.BG, highlightthickness=0, bd=0)
        self.stage.grid(row=0, column=1, sticky="nsew")
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, ui.Page] = {}
        layouts = {
            "download": self._layout_download, "batch": self._layout_batch,
            "queue": self._layout_queue, "updates": self._layout_updates,
        }
        for name, fn in layouts.items():
            page = ui.Page(self.stage, fn, bg=ui.BG)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page

        self._build_sidebar()
        self._build_download_page()
        self._build_batch_page()
        self._build_queue_page()
        self._build_updates_page()

        self.url_entry.focus_set()
        self._sync_mode(self.mode_var.get())
        self.show_page("download")

    @staticmethod
    def _frame(w: int) -> tuple[float, float]:
        cw = max(px(420), min(w - px(64), px(MAX_CONTENT)))
        return (w - cw) / 2 - px(3), cw

    def _header(self, page: ui.Page, name: str) -> tuple[ui.Label, ui.Label]:
        title, subtitle = TITLES[name]
        return (ui.Label(page, title, fg=ui.TEXT, size=26, bold=True),
                ui.Label(page, subtitle, fg=ui.MUTED, size=12))

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> None:
        p = self.side
        self.sb_logo = ui.Card(p, fill=ui.ACCENT, outline=ui.ACCENT, radius=12)
        self.sb_logo_text = ui.Label(p, "MD", fg="#FFFFFF", size=15, bold=True, anchor="center")
        self.sb_name = ui.Label(p, "Media", fg=ui.TEXT, size=16, bold=True)
        self.sb_name2 = ui.Label(p, "Downloader", fg=ui.MUTED, size=12)
        self.nav_buttons: dict[str, ui.Btn] = {}
        for key, label, glyph in NAV:
            self.nav_buttons[key] = ui.Btn(p, label, lambda k=key: self.show_page(k), style="nav", size=13,
                                           icon=glyph if self._icons else None, anchor="w", padx=16)
        self.sb_tools_cap = ui.Label(p, "TOOLS", fg=ui.FAINT, size=9, bold=True)
        self.tool_buttons = [
            ui.Btn(p, label, (lambda n=method: getattr(self, n)()), style="nav", size=13,
                   icon=glyph if self._icons else None, anchor="w", padx=16)
            for label, glyph, method in TOOLS
        ]
        self.top_update_button = ui.Btn(p, "Update available", self.download_or_install_update, style="primary",
                                        size=12)
        self.top_update_button.show(False)
        self.sb_version = ui.Label(p, f"Version {APP_VERSION}", fg=ui.FAINT, size=10)
        self.sb_status = ui.Label(p, "● Local engine ready", fg=ui.SUCCESS, size=10)
        self.sb_divider = ui.Card(p, fill=ui.BORDER_SOFT, outline=ui.BORDER_SOFT, radius=0)

    def _layout_sidebar(self, w: int, h: int) -> int:
        pad = px(14)
        self.sb_logo.place(px(20), px(26), px(44), px(44))
        self.sb_logo_text.place(px(42), px(48))
        self.sb_name.place(px(76), px(25))
        self.sb_name2.place(px(76), px(47))
        y = px(96)
        bw = w - 2 * pad
        for key, _label, _glyph in NAV:
            self.nav_buttons[key].place(pad, y, bw, px(44))
            y += px(48)
        y += px(14)
        self.sb_tools_cap.place(px(26), y)
        y += px(24)
        for button in self.tool_buttons:
            button.place(pad, y, bw, px(42))
            y += px(46)
        bottom = h - px(22)
        self.sb_status.place(px(22), bottom - px(16))
        self.sb_version.place(px(22), bottom - px(36))
        if self.top_update_button.visible:
            self.top_update_button.place(pad, bottom - px(36) - px(54), bw, px(40))
        self.sb_divider.place(w - 1, 0, 1, h)
        return h

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            return
        self.current_page = name
        for key, page in self.pages.items():
            if key == name:
                page.grid()
            else:
                page.grid_remove()
        for key, button in self.nav_buttons.items():
            button.set_active(key == name)
        self.pages[name].request_layout()
        self._render_download_activity()
        if name == "download":
            self.url_entry.focus_set()

    # --------------------------------------------------------- download page
    def _build_download_page(self) -> None:
        p = self.pages["download"]
        self.dl_title, self.dl_sub = self._header(p, "download")
        self.status_chip = ui.Chip(p, "READY", fg=ui.SUCCESS, bg=ui.SUCCESS_SOFT)
        self.status_label = ui.Label(p, "", fg=ui.MUTED, size=11, anchor="ne", ellipsis=True, relayout=False)

        # link card
        self.link_card = ui.Card(p)
        self.link_cap = ui.Label(p, "MEDIA LINK", fg=ui.CYAN, size=10, bold=True)
        self.url_entry = ui.Field(p, var=self.url_var, size=13,
                                  placeholder="Paste any public http:// or https:// media link")
        self.url_entry.bind("<Return>", lambda _e: self.analyze_media())
        self.url_entry.bind("<KeyRelease>", self._on_url_changed)
        self.paste_button = ui.Btn(p, "Paste", self.paste_from_clipboard, style="secondary", size=12)
        self.read_button = ui.Btn(p, "Analyze", self.analyze_media, style="primary", size=12)
        self.cancel_analysis_button = ui.Btn(p, "Cancel", self.cancel_analysis, style="ghost", size=12,
                                             state="disabled")

        # media card
        self.media_card = ui.Card(p)
        self.thumb = ui.Thumb(p)
        self.media_badge = ui.Chip(p, "WAITING FOR LINK", fg=ui.MUTED, bg=ui.SURFACE_2, size=9)
        self.title_label = ui.Label(p, "Analyze a link to see its details", fg=ui.TEXT, size=17, bold=True, wrap=True)
        self.meta_label = ui.Label(p, "Title, creator, platform and duration appear here.", fg=ui.MUTED, size=12,
                                   wrap=True)
        self.media_action_hint = ui.Label(p, "Paste a link — it analyzes automatically.", fg=ui.FAINT, size=11,
                                          wrap=True)

        # options card
        self.opts_card = ui.Card(p)
        self.opts_cap = ui.Label(p, "OUTPUT", fg=ui.CYAN, size=10, bold=True)
        self.cap_dl_type = ui.Label(p, "DOWNLOAD TYPE", fg=ui.MUTED, size=9, bold=True)
        self.mode_control = ui.Segmented(p, ["Video", "Audio"], self.mode_var, command=self._sync_mode)
        self.cap_name = ui.Label(p, "FILE NAME", fg=ui.MUTED, size=9, bold=True)
        self.name_entry = ui.Field(p, var=self.name_var, placeholder="Filled in from the title")
        self.cap_folder = ui.Label(p, "SAVE TO", fg=ui.MUTED, size=9, bold=True)
        self.folder_entry = ui.Field(p, var=self.download_dir_var, readonly=True, fg=ui.MUTED)
        self.choose_folder_button = ui.Btn(p, "Browse", self.choose_download_folder, style="secondary", size=11)
        self.reset_folder_button = ui.Btn(p, "Default", self.reset_download_folder, style="ghost", size=11)
        self.quality_label = ui.Label(p, "VIDEO QUALITY", fg=ui.MUTED, size=9, bold=True)
        self.video_quality = ui.Select(p, ["360p", "480p", "720p", "1080p", "Best available"], self.video_quality_var)
        self.audio_format_label = ui.Label(p, "AUDIO FORMAT", fg=ui.MUTED, size=9, bold=True)
        self.audio_format = ui.Select(p, ["MP3", "M4A"], self.audio_format_var)
        self.audio_quality_label = ui.Label(p, "BITRATE (KBPS)", fg=ui.MUTED, size=9, bold=True)
        self.audio_quality = ui.Select(p, ["128", "192", "256", "320"], self.audio_quality_var)
        self.output_hint = ui.Label(p, "MP4 video • FFmpeg merge when needed", fg=ui.FAINT, size=10)
        self.open_editor_after_check = ui.Check(p, "Open in editor after download", self.open_editor_after_var)
        self.download_button = ui.Btn(p, "Download video", self.download, style="cyan", size=13, state="disabled")

        # recent downloads
        self.recent_card = ui.Card(p)
        self.recent_cap = ui.Label(p, "RECENT DOWNLOADS", fg=ui.CYAN, size=10, bold=True)
        self.open_file_button = ui.Btn(p, "Open last file", self.open_last_file, style="ghost", size=10,
                                       state="disabled")
        self.open_folder_button = ui.Btn(p, "Open folder", self.open_download_folder, style="ghost", size=10)
        self.view_all_button = ui.Btn(p, "View all", lambda: self.show_page("queue"), style="secondary", size=10)
        self.recent_list = ActivityList(p, on_cancel=self.cancel_download, on_retry=self.retry_download,
                                       on_pause=self.pause_download, on_resume=self.resume_download,
                                        empty_text="Nothing downloaded yet — paste a link above.")
        self.status_label.show(False)

    def _layout_download(self, w: int, h: int) -> int:
        x0, cw = self._frame(w)
        pad = px(22)
        y = px(30)
        self.dl_title.place(x0, y)
        self.dl_sub.place(x0, y + px(42))
        chip_w = self.status_chip.natural_w()
        self.status_chip.place(x0 + cw - chip_w, y + px(14))
        if self.status_label.visible:
            self.status_label.place(x0 + cw - chip_w - px(12), y + px(17), max(60, min(px(420), cw - chip_w - px(300))))
        y += px(96)

        # link card
        card_h = px(126)
        self.link_card.place(x0, y, cw, card_h)
        self.link_cap.place(x0 + pad, y + px(20))
        bx = x0 + cw - pad
        for button in (self.cancel_analysis_button, self.read_button, self.paste_button):
            bw = button.natural_w(minimum=88 if button is not self.paste_button else 80)
            button.place(bx - bw, y + px(52), bw, px(48))
            bx -= bw + px(10)
        self.url_entry.place(x0 + pad, y + px(52), bx - (x0 + pad) + px(6), px(48))
        y += card_h + px(16)

        # media card
        tx = x0 + pad + px(THUMB_W) + pad
        tw = x0 + cw - pad - tx
        self.thumb.place(x0 + pad, y + pad, px(THUMB_W), px(THUMB_H))
        self.media_badge.place(tx, y + pad)
        ty = y + px(54)
        h1 = self.title_label.place(tx, ty, tw)
        h2 = self.meta_label.place(tx, ty + h1 + px(6), tw)
        h3 = self.media_action_hint.place(tx, ty + h1 + px(6) + h2 + px(12), tw)
        text_h = (ty - y) + h1 + px(6) + h2 + px(12) + h3 + pad
        media_h = max(px(THUMB_H) + 2 * pad, text_h)
        self.media_card.place(x0, y, cw, media_h)
        y += media_h + px(16)

        # options card
        top = y
        col_gap = px(26)
        col_w = (cw - 2 * pad - col_gap) / 2
        lx, rx = x0 + pad, x0 + pad + col_w + col_gap
        self.opts_cap.place(lx, top + px(20))
        r0 = top + px(54)
        self.cap_dl_type.place(lx, r0)
        self.mode_control.place(lx, r0 + px(22), col_w, px(46))
        self.cap_name.place(lx, r0 + px(82))
        self.name_entry.place(lx, r0 + px(104), col_w, px(46))
        self.cap_folder.place(lx, r0 + px(164))
        browse_w = self.choose_folder_button.natural_w(minimum=84)
        default_w = self.reset_folder_button.natural_w(minimum=76)
        self.reset_folder_button.place(lx + col_w - default_w, r0 + px(186), default_w, px(46))
        self.choose_folder_button.place(lx + col_w - default_w - px(8) - browse_w, r0 + px(186), browse_w, px(46))
        self.folder_entry.place(lx, r0 + px(186), col_w - default_w - browse_w - px(16), px(46))

        self.quality_label.place(rx, r0)
        self.video_quality.place(rx, r0 + px(22), col_w, px(46))
        half = (col_w - px(12)) / 2
        self.audio_format_label.place(rx, r0 + px(82))
        self.audio_quality_label.place(rx + half + px(12), r0 + px(82))
        self.audio_format.place(rx, r0 + px(104), half, px(46))
        self.audio_quality.place(rx + half + px(12), r0 + px(104), half, px(46))
        self.output_hint.place(rx, r0 + px(168))

        foot = r0 + px(254)
        self.open_editor_after_check.place(lx, foot, px(48))
        dl_w = max(px(200), self.download_button.natural_w())
        self.download_button.place(x0 + cw - pad - dl_w, foot, dl_w, px(48))
        opts_h = (foot + px(48) + pad) - top
        self.opts_card.place(x0, top, cw, opts_h)
        y = top + opts_h + px(16)

        # recent downloads
        top = y
        self.recent_cap.place(x0 + pad, top + px(24))
        bx = x0 + cw - pad
        for button in (self.view_all_button, self.open_folder_button, self.open_file_button):
            bw = button.natural_w(minimum=80)
            button.place(bx - bw, top + px(16), bw, px(32))
            bx -= bw + px(8)
        list_h = self.recent_list.place(x0 + pad, top + px(62), cw - 2 * pad)
        recent_h = px(62) + list_h + pad
        self.recent_card.place(x0, top, cw, recent_h)
        return int(top + recent_h + px(34))

    # ------------------------------------------------------------ batch page
    def _build_batch_page(self) -> None:
        p = self.pages["batch"]
        self.batch_title, self.batch_sub = self._header(p, "batch")
        self.batch_page = BatchSection(
            p,
            on_enqueue=self._enqueue_batch_downloads,
            on_cancel=self.cancel_download,
            on_retry=self.retry_download,
            on_pause=self.pause_download,
            on_resume=self.resume_download,
            get_snapshots=self.download_queue.snapshot,
            settings=self.settings,
        )

    def _layout_batch(self, w: int, h: int) -> int:
        x0, cw = self._frame(w)
        y = px(30)
        self.batch_title.place(x0, y)
        self.batch_sub.place(x0, y + px(42))
        end = self.batch_page.layout(x0, y + px(96), cw)
        return int(end + px(34))

    # ------------------------------------------------------------ queue page
    def _build_queue_page(self) -> None:
        p = self.pages["queue"]
        self.queue_title, self.queue_sub = self._header(p, "queue")
        self.downloads_summary = ui.Label(p, "0 active • 0 queued", fg=ui.CYAN, size=12, bold=True, anchor="ne",
                                          relayout=False)
        self.cancel_all_downloads_button = ui.Btn(p, "Cancel all", self.cancel_all_downloads, style="danger",
                                                  size=11, state="disabled")
        self.clear_finished_button = ui.Btn(p, "Clear finished", self.clear_finished_downloads, style="ghost",
                                            size=11)
        self.pause_all_button = ui.Btn(p, "Pause all", self.pause_all_downloads, style="secondary", size=11)
        self.resume_all_button = ui.Btn(p, "Resume all", self.resume_all_downloads, style="secondary", size=11)
        self.queue_card = ui.Card(p)
        self.queue_list = ActivityList(p, on_cancel=self.cancel_download, on_retry=self.retry_download,
                                       on_pause=self.pause_download, on_resume=self.resume_download,
                                       empty_text="No downloads yet. Analyze a link and press Download.")

    def _layout_queue(self, w: int, h: int) -> int:
        x0, cw = self._frame(w)
        pad = px(22)
        y = px(30)
        self.queue_title.place(x0, y)
        self.queue_sub.place(x0, y + px(42))
        bx = x0 + cw
        for button in (self.cancel_all_downloads_button, self.clear_finished_button,
                       self.resume_all_button, self.pause_all_button):
            bw = button.natural_w(minimum=96)
            button.place(bx - bw, y + px(12), bw, px(38))
            bx -= bw + px(8)
        self.downloads_summary.place(bx - px(10), y + px(21))
        y += px(96)
        list_h = self.queue_list.place(x0 + pad, y + pad, cw - 2 * pad)
        card_h = list_h + 2 * pad
        self.queue_card.place(x0, y, cw, card_h)
        return int(y + card_h + px(34))

    # ---------------------------------------------------------- updates page
    def _build_updates_page(self) -> None:
        p = self.pages["updates"]
        self.up_title, self.up_sub = self._header(p, "updates")
        self.update_card_bg = ui.Card(p)
        self.update_status_label = ui.Label(p, f"Current version: v{APP_VERSION}", fg=ui.TEXT, size=15, bold=True)
        self.update_check_button = ui.Btn(p, "Check for updates", lambda: self.check_for_updates(manual=True),
                                          style="secondary", size=11)
        self.update_detail_label = ui.Label(
            p,
            "Automatic update checks are enabled." if self.auto_check_updates_var.get()
            else "Automatic update checks are disabled.",
            fg=ui.MUTED, size=11, wrap=True)
        self.update_notes_label = ui.Label(p, "", fg="#AFC0D9", size=11, wrap=True)
        self.update_progress = ui.Bar(p, height=8, color=ui.CYAN)
        self.update_action_button = ui.Btn(p, "Update now", self.download_or_install_update, style="primary",
                                           size=12, state="disabled")
        self.update_later_button = ui.Btn(p, "Later", self.snooze_update, style="ghost", size=12, state="disabled")
        self.update_cancel_button = ui.Btn(p, "Cancel download", self.cancel_update_download, style="ghost",
                                           size=12, state="disabled")
        self.release_button = ui.Btn(p, "Open latest release", self.open_release_page, style="ghost", size=12)

        self.about_card = ui.Card(p)
        self.about_cap = ui.Label(p, "ABOUT", fg=ui.CYAN, size=10, bold=True)
        self.save_location_label = ui.Label(p, f"Saving to  {self.download_dir}", fg=ui.MUTED, size=11,
                                            ellipsis=True, relayout=False)
        self.about_note = ui.Label(p, "Use only for content you own or have permission to download.", fg=ui.FAINT,
                                   size=11)
        self.diag_button = ui.Btn(p, "Diagnostics", self.open_diagnostics, style="secondary", size=11)
        self.dev_button = ui.Btn(p, "Developed by Md Omar Faruk  ↗",
                                 lambda: webbrowser.open("https://github.com/umar-vai"), style="ghost", size=11)

    def _layout_updates(self, w: int, h: int) -> int:
        x0, cw = self._frame(w)
        pad = px(22)
        y = px(30)
        self.up_title.place(x0, y)
        self.up_sub.place(x0, y + px(42))
        top = y + px(96)
        inner_w = cw - 2 * pad
        self.update_status_label.place(x0 + pad, top + px(24))
        cb = self.update_check_button.natural_w(minimum=150)
        self.update_check_button.place(x0 + cw - pad - cb, top + px(16), cb, px(40))
        cy = top + px(70)
        cy += self.update_detail_label.place(x0 + pad, cy, inner_w) + px(8)
        if self.update_notes_label.text:
            cy += self.update_notes_label.place(x0 + pad, cy, inner_w) + px(8)
        self.update_progress.place(x0 + pad, cy + px(6), inner_w)
        cy += px(32)
        bx = x0 + pad
        for button in (self.update_action_button, self.update_later_button, self.update_cancel_button,
                       self.release_button):
            bw = button.natural_w(minimum=96)
            button.place(bx, cy, bw, px(42))
            bx += bw + px(8)
        card_h = (cy + px(42) + pad) - top
        self.update_card_bg.place(x0, top, cw, card_h)

        top = top + card_h + px(16)
        self.about_cap.place(x0 + pad, top + px(22))
        self.save_location_label.place(x0 + pad, top + px(52), inner_w)
        self.about_note.place(x0 + pad, top + px(76))
        dw = self.diag_button.natural_w(minimum=110)
        self.diag_button.place(x0 + pad, top + px(112), dw, px(38))
        self.dev_button.place(x0 + pad + dw + px(8), top + px(112), self.dev_button.natural_w(), px(38))
        about_h = px(112) + px(38) + pad
        self.about_card.place(x0, top, cw, about_h)
        return int(top + about_h + px(34))

    # ------------------------------------------------------------- behaviour
    def _sync_mode(self, value: str | None = None) -> None:
        mode = value or self.mode_var.get()
        is_video = mode == "Video"
        self.mode_var.set(mode)
        self.video_quality.configure(state="normal" if is_video else "disabled")
        self.audio_format.configure(state="disabled" if is_video else "normal")
        self.audio_quality.configure(state="disabled" if is_video else "normal")
        self.quality_label.configure(text_color=ui.MUTED if is_video else ui.FAINT)
        self.audio_format_label.configure(text_color=ui.FAINT if is_video else ui.MUTED)
        self.audio_quality_label.configure(text_color=ui.FAINT if is_video else ui.MUTED)
        self.download_button.configure(text="Download video" if is_video else "Download audio")
        self.output_hint.configure(
            text="MP4 video • FFmpeg merge when needed" if is_video else "Audio only • choose format and bitrate"
        )

    def _set_status(self, text: str, kind: str = "ready") -> None:
        palette = {
            "ready": (ui.SUCCESS_SOFT, ui.SUCCESS, "READY"),
            "working": (ui.INFO_SOFT, ui.CYAN, "WORKING"),
            "success": (ui.SUCCESS_SOFT, ui.SUCCESS, "COMPLETE"),
            "cancelled": (ui.WARNING_SOFT, ui.WARNING, "CANCELLED"),
            "error": (ui.DANGER_SOFT, ui.DANGER, "FAILED"),
        }
        bg, fg, chip = palette.get(kind, palette["ready"])
        self.status_chip.configure(text=chip, fg_color=bg, text_color=fg)
        self.status_label.configure(text=text)
        self.status_label.show(bool(text))

    def _schedule_download_activity_refresh(self, delay_ms: int = 120) -> None:
        if self._download_activity_after_id is not None:
            return

        def run() -> None:
            self._download_activity_after_id = None
            self._render_download_activity()

        self._download_activity_after_id = self.after(max(60, int(delay_ms)), run)

    def _render_download_activity(self) -> None:
        if not hasattr(self, "recent_list"):
            return
        snapshots = self.download_queue.snapshot()
        active = self.download_queue.running_count()
        queued = self.download_queue.queued_count()
        busy = active + queued

        self.downloads_summary.configure(text=f"{active} active • {queued} queued")
        self.cancel_all_downloads_button.configure(state="normal" if busy else "disabled")
        self.nav_buttons["queue"].configure(text=f"Queue  ({busy})" if busy else "Queue")

        page = self.current_page
        if page == "download":
            self.recent_list.sync(snapshots, limit=4)
        elif page == "queue":
            self.queue_list.sync(snapshots)
        elif page == "batch":
            self.batch_page.refresh(snapshots)
        self._after_activity_render(snapshots, active, queued)

    # ------------------------------------------------------------- thumbnail
    def _reset_thumbnail_label(self) -> None:
        self.thumbnail_image = None
        self.thumb.set_image(None)

    def _apply_thumbnail(self, raw: bytes | None) -> None:
        if not raw:
            self._reset_thumbnail_label()
            return
        try:
            width, height = px(THUMB_W) - 2, px(THUMB_H) - 2
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.thumbnail((width, height), Image.Resampling.BILINEAR)
            canvas = Image.new("RGB", (width, height), (23, 30, 46))
            canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
            photo = ImageTk.PhotoImage(canvas)
            self.thumbnail_image = photo
            self.thumb.set_image(photo)
        except Exception:
            self._reset_thumbnail_label()
