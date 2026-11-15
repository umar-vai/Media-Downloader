"""The "Add batch download" dialog: paste many links (or load a .txt) and queue them.

Progress of a batch is shown in the main download list under Queues.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import native
from batch_download import MAX_BATCH_URLS, parse_batch_urls_detailed
from native import GAP, PAD
from theme import C

VIDEO_QUALITIES = ["Best available", "1080p", "720p", "480p", "360p"]


class BatchSection:
    def __init__(
        self,
        dialog: native.Dialog,
        *,
        on_enqueue: Callable[[str, dict[str, str]], tuple[str, int, int]],
        settings: dict[str, Any],
        on_queued: Callable[[str], None] | None = None,
    ) -> None:
        self.dialog = dialog
        self._on_enqueue = on_enqueue
        self._on_queued = on_queued
        self.mode_var = tk.StringVar(dialog, value=str(settings.get("default_mode") or "Video"))
        self.quality_var = tk.StringVar(dialog, value=str(settings.get("video_quality") or "720p"))
        self.audio_format_var = tk.StringVar(dialog, value=str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var = tk.StringVar(dialog, value=str(settings.get("audio_quality") or "192"))

        body = dialog.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        ttk.Label(body, text="Links to download (one per line, up to 300):").grid(row=0, column=0, sticky="w")
        self.count_label = ttk.Label(body, text=f"0 / {MAX_BATCH_URLS} links", style="Hint.TLabel")
        self.count_label.grid(row=0, column=1, sticky="e")
        frame = ttk.Frame(body)
        frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(GAP, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.text = tk.Text(frame, width=80, height=14, wrap="none", undo=True, font=("Consolas", 10),
                            relief="flat", bd=0, highlightthickness=1, highlightbackground=C["BORDER"],
                            highlightcolor=C["ACCENT"], bg=C["SURFACE"], fg=C["TEXT"], insertbackground=C["TEXT"])
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.bind("<KeyRelease>", self._update_count)
        self.text.bind("<<Paste>>", lambda _e: self.text.after(40, self._after_paste), add="+")

        tools = ttk.Frame(body)
        tools.grid(row=2, column=0, columnspan=2, sticky="w", pady=(GAP, 0))
        ttk.Button(tools, text="Paste", command=self._paste, width=9).pack(side="left")
        ttk.Button(tools, text="Load from file…", command=self._load_file, width=15).pack(side="left", padx=(GAP, 0))
        ttk.Button(tools, text="Clear", command=self._clear, width=9).pack(side="left", padx=(GAP, 0))

        save = native.group(body, "Save as", row=3, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
        ttk.Label(save, text="Type:").grid(row=0, column=0, sticky="w", padx=(0, PAD), pady=2)
        kinds = ttk.Frame(save)
        kinds.grid(row=0, column=1, columnspan=3, sticky="w", pady=2)
        for value in ("Video", "Audio"):
            ttk.Radiobutton(kinds, text=value, value=value, variable=self.mode_var,
                            command=self._sync_mode).pack(side="left", padx=(0, PAD * 2))
        ttk.Label(save, text="Video quality:").grid(row=1, column=0, sticky="w", padx=(0, PAD), pady=2)
        self.quality_select = native.combo(save, self.quality_var, VIDEO_QUALITIES, width=16)
        self.quality_select.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(save, text="Audio format:").grid(row=2, column=0, sticky="w", padx=(0, PAD), pady=2)
        self.format_select = native.combo(save, self.audio_format_var, ["MP3", "M4A", "FLAC", "OPUS", "WAV"], width=8)
        self.format_select.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(save, text="Bitrate (kbps):").grid(row=2, column=2, sticky="w", padx=(PAD * 2, GAP), pady=2)
        self.bitrate_select = native.combo(save, self.audio_quality_var, ["128", "192", "256", "320"], width=6)
        self.bitrate_select.grid(row=2, column=3, sticky="w", pady=2)

        self.status_label = dialog.status
        self.status_label.configure(text="Each batch shows up in the list under Queues.")
        dialog.add_button("Close", dialog.close)
        self.start_button = dialog.add_button("Add to queue", self._enqueue, default=True, width=14)
        self._sync_mode()

    # form helpers ----------------------------------------------------------------
    def _sync_mode(self) -> None:
        audio = self.mode_var.get() == "Audio"
        self.quality_select.configure(state="disabled" if audio else "readonly")
        self.format_select.configure(state="readonly" if audio else "disabled")
        self.bitrate_select.configure(state="readonly" if audio else "disabled")

    def set_defaults(self, settings: dict[str, Any]) -> None:
        self.mode_var.set(str(settings.get("default_mode") or "Video"))
        self.quality_var.set(str(settings.get("video_quality") or "720p"))
        self.audio_format_var.set(str(settings.get("audio_format") or "MP3"))
        self.audio_quality_var.set(str(settings.get("audio_quality") or "192"))
        self._sync_mode()

    def _text(self) -> str:
        return self.text.get("1.0", "end-1c")

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
                                   style="Error.TLabel" if count > MAX_BATCH_URLS else "Hint.TLabel")

    def _after_paste(self) -> None:
        """Move the cursor to a fresh line after a pasted link so the next paste lands on its own line."""
        text = self.text
        try:
            before_cursor = text.get("insert linestart", "insert")
            after_cursor = text.get("insert", "insert lineend")
            if before_cursor.strip() and not after_cursor.strip():
                text.insert("insert", "\n")
                text.mark_set("insert", "insert lineend")
                text.see("insert")
        except tk.TclError:
            pass
        self._update_count()

    def _append(self, value: str) -> None:
        existing = self._text()
        if existing and not existing.endswith("\n"):
            self.text.insert("end-1c", "\n")
        self.text.insert("end-1c", value.strip() + "\n")
        self.text.see("end")
        self._update_count()

    def _paste(self) -> None:
        try:
            value = self.dialog.clipboard_get()
        except Exception:
            return
        self._append(value)

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.dialog, title="Load links from a text file",
                                          filetypes=[("Text files", "*.txt *.csv *.list *.md"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                content = handle.read(2_000_000)
        except OSError as exc:
            messagebox.showerror("Batch download", f"Could not read the file. {exc}", parent=self.dialog)
            return
        self._append(content)

    def _clear(self) -> None:
        self.text.delete("1.0", "end")
        self._update_count()

    def _enqueue(self) -> None:
        try:
            urls, duplicates, invalid = parse_batch_urls_detailed(self._text())
        except ValueError as exc:
            messagebox.showerror("Batch download", str(exc), parent=self.dialog)
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
            messagebox.showerror("Batch download", str(exc), parent=self.dialog)
            return
        note = f"Queued {queued} link{'s' if queued != 1 else ''}"
        if duplicates:
            note += f" • {duplicates} duplicate skipped"
        if invalid:
            note += f" • {invalid} invalid skipped"
        self.status_label.configure(text=note)
        self._clear()
        if self._on_queued:
            self._on_queued(batch_id)

    def refresh(self, snapshots: list[dict[str, Any]] | None = None) -> None:
        """Kept for compatibility: batch progress lives in the main list (Queues)."""

    def focus(self) -> None:
        self.text.focus_set()
