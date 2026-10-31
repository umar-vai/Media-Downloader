from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk
from tkinter import messagebox

from batch_download import batch_summaries, parse_batch_urls

BG = "#060B14"
SURFACE = "#0B1323"
SURFACE_2 = "#101C31"
BORDER = "#223456"
TEXT = "#F7FAFF"
MUTED = "#8798B5"
CYAN = "#23D5FF"
PURPLE = "#7657FF"
WARNING = "#FFB84D"
DANGER = "#FF647C"
SUCCESS = "#24D18C"

BatchCallback = Callable[[str, dict[str, str]], tuple[str, int, int]]
ItemCallback = Callable[[str], None]
SnapshotCallback = Callable[[], list[dict[str, Any]]]


class BatchDownloadWindow(ctk.CTkToplevel):
    """Native Windows desktop batch manager. All UI work stays on Tk's thread."""

    def __init__(
        self,
        master: Any,
        *,
        on_enqueue: BatchCallback,
        on_cancel: ItemCallback,
        on_retry: ItemCallback,
        get_snapshots: SnapshotCallback,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(master)
        self.title("Batch Download | Media Downloader")
        self.geometry("960x730")
        self.minsize(750, 580)
        self.configure(fg_color=BG)
        self._on_enqueue = on_enqueue
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._get_snapshots = get_snapshots
        self._after_id: str | None = None
        self._group_ids: dict[str, str] = {}
        self._selected_group = ""
        self._last_signature: Any = None

        self.mode_var = ctk.StringVar(value=str(settings.get("default_mode") or "Video"))
        self.quality_var = ctk.StringVar(value=str(settings.get("video_quality") or "720p"))
        self.audio_format_var = ctk.StringVar(value=str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var = ctk.StringVar(value=str(settings.get("audio_quality") or "192"))
        self.batch_choice_var = ctk.StringVar(value="No batches yet")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 10))
        ctk.CTkLabel(
            header, text="BATCH DOWNLOAD", text_color=TEXT, font=("Segoe UI Semibold", 23)
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Paste up to 50 URLs. Analysis and downloads share the normal 3-slot queue.",
            text_color=MUTED, font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(3, 0))

        form = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        form.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        form.grid_columnconfigure(0, weight=1)
        form.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(form, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        toolbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(toolbar, text="ONE URL PER LINE", text_color=CYAN, font=("Segoe UI Semibold", 10)).grid(
            row=0, column=0, sticky="w"
        )
        self.count_label = ctk.CTkLabel(toolbar, text="0 / 50", text_color=MUTED, font=("Segoe UI", 10))
        self.count_label.grid(row=0, column=1, sticky="e")
        self.url_box = ctk.CTkTextbox(form, height=130, fg_color=SURFACE_2, text_color=TEXT,
                                     border_width=1, border_color=BORDER, font=("Consolas", 12))
        self.url_box.grid(row=1, column=0, sticky="nsew", padx=14)
        self.url_box.bind("<KeyRelease>", self._update_url_count)

        options = ctk.CTkFrame(form, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=14, pady=(12, 9))
        for i in range(4):
            options.grid_columnconfigure(i, weight=1)

        controls = (
            ("OUTPUT TYPE", self.mode_var, ["Video", "Audio"]),
            ("VIDEO QUALITY", self.quality_var, ["Best available", "1080p", "720p", "480p", "360p"]),
            ("AUDIO FORMAT", self.audio_format_var, ["MP3", "M4A"]),
            ("BITRATE", self.audio_quality_var, ["128", "192", "256", "320"]),
        )
        self.inputs: list[ctk.CTkOptionMenu] = []
        for i, (label, variable, values) in enumerate(controls):
            cell = ctk.CTkFrame(options, fg_color="transparent")
            cell.grid(row=0, column=i, sticky="ew", padx=(0, 7) if i < 3 else (0, 0))
            ctk.CTkLabel(cell, text=label, text_color=MUTED, font=("Segoe UI Semibold", 9)).pack(anchor="w")
            control = ctk.CTkOptionMenu(
                cell, variable=variable, values=values, height=34, corner_radius=9,
                fg_color=SURFACE_2, button_color="#263E65", button_hover_color=PURPLE,
                text_color=TEXT, dropdown_fg_color=SURFACE_2, dropdown_text_color=TEXT,
            )
            control.pack(fill="x", pady=(5, 0))
            self.inputs.append(control)
        self.mode_var.trace_add("write", lambda *_: self._sync_mode())
        self._sync_mode()

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 13))
        self.start_button = ctk.CTkButton(
            actions, text="Add batch to queue", width=166, height=38,
            fg_color=PURPLE, hover_color="#6547E9", command=self._enqueue,
        )
        self.start_button.pack(side="left")
        ctk.CTkButton(actions, text="Paste list", width=94, height=38,
                      fg_color=SURFACE_2, command=self._paste).pack(side="left", padx=(8, 0))
        ctk.CTkButton(actions, text="Clear", width=70, height=38,
                      fg_color=SURFACE_2, command=self._clear).pack(side="left", padx=(8, 0))
        self.status_label = ctk.CTkLabel(
            actions, text="Ready", text_color=MUTED, font=("Segoe UI", 10), anchor="e"
        )
        self.status_label.pack(side="right")

        section = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        section.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 17))
        section.grid_columnconfigure(0, weight=1)
        section.grid_rowconfigure(2, weight=1)

        group_toolbar = ctk.CTkFrame(section, fg_color="transparent")
        group_toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        group_toolbar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(group_toolbar, text="BATCH PROGRESS", text_color=CYAN,
                     font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w")
        self.group_choice = ctk.CTkOptionMenu(
            group_toolbar, variable=self.batch_choice_var, values=["No batches yet"],
            command=self._choose_batch, width=200, height=30,
            fg_color=SURFACE_2, button_color="#263E65", text_color=TEXT,
        )
        self.group_choice.grid(row=0, column=1, sticky="e", padx=8)
        self.cancel_all_button = ctk.CTkButton(
            group_toolbar, text="Cancel remaining", width=125, height=30,
            fg_color=SURFACE_2, text_color=WARNING, command=self._cancel_group,
        )
        self.cancel_all_button.grid(row=0, column=2, padx=(0, 6))
        self.retry_all_button = ctk.CTkButton(
            group_toolbar, text="Retry failed", width=105, height=30,
            fg_color=SURFACE_2, text_color=TEXT, command=self._retry_group,
        )
        self.retry_all_button.grid(row=0, column=3)

        summary = ctk.CTkFrame(section, fg_color="transparent")
        summary.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        summary.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(
            summary, text="No downloads queued", font=("Segoe UI", 11), text_color=MUTED
        )
        self.summary_label.grid(row=0, column=0, sticky="w")
        self.overall_progress = ctk.CTkProgressBar(summary, height=7, progress_color=PURPLE)
        self.overall_progress.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.overall_progress.set(0)

        self.items_frame = ctk.CTkScrollableFrame(
            section, fg_color="transparent", height=210, scrollbar_button_color="#243957"
        )
        self.items_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 13))
        self.items_frame.grid_columnconfigure(0, weight=1)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh()

    def _sync_mode(self) -> None:
        audio = self.mode_var.get() == "Audio"
        self.inputs[1].configure(state="disabled" if audio else "normal")
        for control in self.inputs[2:]:
            control.configure(state="normal" if audio else "disabled")

    def _update_url_count(self, _event: Any = None) -> None:
        count = len(self.url_box.get("1.0", "end-1c").split())
        self.count_label.configure(text=f"{count} / 50", text_color=DANGER if count > 50 else MUTED)

    def _paste(self) -> None:
        try:
            value = self.clipboard_get()
        except Exception:
            return
        self.url_box.insert("end", ("\n" if self.url_box.get("1.0", "end-1c").strip() else "") + value)
        self._update_url_count()

    def _clear(self) -> None:
        self.url_box.delete("1.0", "end")
        self._update_url_count()

    def _enqueue(self) -> None:
        text = self.url_box.get("1.0", "end-1c")
        try:
            urls, skipped = parse_batch_urls(text)
            self.start_button.configure(state="disabled")
            batch_id, queued, _duplicates = self._on_enqueue(
                "\n".join(urls),
                {
                    "mode": self.mode_var.get(),
                    "video_quality": self.quality_var.get(),
                    "audio_format": self.audio_format_var.get(),
                    "audio_quality": self.audio_quality_var.get(),
                },
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("Batch Download", str(exc), parent=self)
            return
        finally:
            self.start_button.configure(state="normal")
        self.status_label.configure(
            text=f"Queued {queued} • Skipped {skipped} duplicate(s)", text_color=SUCCESS
        )
        self._clear()
        self._selected_group = batch_id
        self._last_signature = None
        self._refresh()

    def _choose_batch(self, choice: str) -> None:
        self._selected_group = self._group_ids.get(choice, "")
        self._last_signature = None
        self._refresh()

    def _cancel_group(self) -> None:
        for item in self._get_snapshots():
            if item.get("batch_id") == self._selected_group and item.get("status") in {"queued","running","cancelling"}:
                self._on_cancel(str(item["id"]))
        self._refresh()

    def _retry_group(self) -> None:
        for item in self._get_snapshots():
            if item.get("batch_id") == self._selected_group and item.get("status") == "failed":
                self._on_retry(str(item["id"]))
        self._refresh()

    def _refresh(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if not self.winfo_exists():
            return
        batches = batch_summaries(self._get_snapshots())
        choices = [f"{b['batch_id'][:8]} • {b['total']} items" for b in batches]
        self._group_ids = {label: summary["batch_id"] for label, summary in zip(choices, batches)}
        self.group_choice.configure(values=choices or ["No batches yet"])
        selected = next((batch for batch in batches if batch["batch_id"] == self._selected_group), None)
        if selected is None and batches:
            selected = batches[0]
            self._selected_group = selected["batch_id"]
        if selected:
            label = next(label for label, bid in self._group_ids.items() if bid == self._selected_group)
            self.batch_choice_var.set(label)
        else:
            self.batch_choice_var.set("No batches yet")

        self.cancel_all_button.configure(state="normal" if selected and selected["active"] else "disabled")
        self.retry_all_button.configure(state="normal" if selected and selected["failed"] else "disabled")
        if selected:
            self.summary_label.configure(
                text=f"{selected['completed']} done • {selected['failed']} failed • "
                     f"{selected['cancelled']} cancelled • {selected['active']} active/queued"
            )
            self.overall_progress.set(float(selected["progress"]))
        else:
            self.summary_label.configure(text="No batches submitted")
            self.overall_progress.set(0)

        items = selected["items"] if selected else []
        signature = (
            self._selected_group,
            tuple(
                (item["id"], item["status"], round(float(item.get("progress") or 0), 2),
                 item["name"], item["detail"], item["error"])
                for item in items
            ),
        )
        if signature != self._last_signature:
            self._last_signature = signature
            for widget in self.items_frame.winfo_children():
                widget.destroy()
            for index, item in enumerate(items):
                status = str(item.get("status") or "")
                row = ctk.CTkFrame(self.items_frame, fg_color=SURFACE_2, corner_radius=9)
                row.grid(row=index, column=0, sticky="ew", pady=(0, 5))
                row.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(
                    row, text=f"{index + 1:02d}. {str(item.get('name') or '')[:84]}",
                    font=("Segoe UI", 10), anchor="w", text_color=TEXT
                ).grid(row=0, column=0, sticky="ew", padx=(9, 4), pady=(7, 0))
                ctk.CTkLabel(
                    row, text=status.upper(), text_color=(
                        SUCCESS if status == "completed" else DANGER if status == "failed" else
                        CYAN if status == "running" else WARNING if status == "cancelled" else MUTED
                    ), font=("Segoe UI Semibold", 9)
                ).grid(row=0, column=1, padx=7, pady=(7, 0))
                ctk.CTkProgressBar(
                    row, height=5, progress_color=PURPLE
                ).grid(row=1, column=0, sticky="ew", padx=(9, 4), pady=(4, 7))
                progress = row.grid_slaves(row=1, column=0)[0]
                progress.set(1.0 if status == "completed" else float(item.get("progress") or 0))
                detail = str(item.get("error") or item.get("detail") or "")
                ctk.CTkLabel(
                    row, text=detail[:110], text_color=MUTED, font=("Segoe UI", 9),
                    anchor="w"
                ).grid(row=2, column=0, sticky="ew", padx=9, pady=(0, 7))
                if status in {"queued", "running", "cancelling"}:
                    ctk.CTkButton(
                        row, text="Cancel", width=65, height=26, fg_color="transparent",
                        border_width=1, border_color=BORDER, text_color=WARNING,
                        state="disabled" if status == "cancelling" else "normal",
                        command=lambda rid=item["id"]: self._on_cancel(rid),
                    ).grid(row=1, column=1, rowspan=2, padx=8)
                elif status == "failed":
                    ctk.CTkButton(
                        row, text="Retry", width=65, height=26, fg_color=PURPLE,
                        command=lambda rid=item["id"]: self._on_retry(rid),
                    ).grid(row=1, column=1, rowspan=2, padx=8)

        self._after_id = self.after(1150, self._refresh)

    def _close(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.destroy()
