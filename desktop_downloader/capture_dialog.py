"""The small "New download" window shown when the browser hands a download to the app.

Like Internet Download Manager: first it asks what to call the file and where to save it
(Download Now / Download Later / Cancel). After "Download Now" the same window turns into a
progress window (pause, cancel, minimize) and, when the file is finished, into a result
window (open, open folder, delete).
"""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import download_list as dl
import native
from native import GAP, PAD

TICK_MS = 400
LIVE = {"queued", "running", "cancelling"}


class CaptureApi:
    """What the window needs from the app (kept as plain callables so it is easy to test)."""

    def __init__(
        self,
        *,
        start: Callable[[str, str, bool, dict[str, bool]], str | None],
        state: Callable[[str], tuple[dict[str, Any] | None, dict[str, Any]]],
        pause: Callable[[str], None],
        resume: Callable[[str], None],
        cancel: Callable[[str], None],
        retry: Callable[[str], None],
        deleted: Callable[[str, str], None],
    ) -> None:
        self.start = start
        self.state = state
        self.pause = pause
        self.resume = resume
        self.cancel = cancel
        self.retry = retry
        self.deleted = deleted


def open_path(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        messagebox.showerror("Media Downloader", f"Could not open it.\n\n{exc}")


def reveal(path: str) -> None:
    target = Path(os.path.normpath(path))
    try:
        if os.name == "nt":
            if target.is_file():
                subprocess.Popen(f'explorer /select,"{target}"')
            else:
                os.startfile(str(target if target.is_dir() else target.parent))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
    except Exception:
        pass


class CaptureDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        api: CaptureApi,
        *,
        name: str,
        folder: str,
        source: str,
        size_bytes: int = 0,
        is_file: bool = True,
        offset: int = 0,
        ext: str = "",
        host: str = "",
        ext_saved: bool = False,
        site_saved: bool = False,
    ) -> None:
        super().__init__(master)
        self.withdraw()
        self._api = api
        self._request_id = ""
        self._path = ""
        self._mode = "prompt"
        self._closed = False
        self._tick_job: str | None = None
        self._last_status = ""
        self._is_file = is_file
        self._orig_ext = os.path.splitext(name)[1] if is_file else ""
        self.title("New download")
        self.configure(bg=native.dialog_bg(self))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_escape())

        self._ext = ext
        self._host = host
        # Ticked when this file type / site already saves into the folder shown here.
        self.remember_ext = tk.BooleanVar(self, value=bool(ext_saved))
        self.remember_site = tk.BooleanVar(self, value=bool(site_saved))
        self.name_var = tk.StringVar(self, value=name)
        self.folder_var = tk.StringVar(self, value=folder)
        self._source = source
        self._size_bytes = size_bytes

        self.body = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        self.body.pack(side="top", fill="both", expand=True)
        self.buttons = ttk.Frame(self, padding=(PAD, PAD + 2, PAD, PAD))
        self.buttons.pack(side="bottom", fill="x")
        self.status = ttk.Label(self.buttons, text="", style="Hint.TLabel")
        self.status.pack(side="left", fill="x", expand=True)
        self._button_widgets: list[ttk.Button] = []

        self._build_prompt()
        self._place(offset)

    # ------------------------------------------------------------------ helpers
    def _place(self, offset: int) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        step = 28 * (offset % 8)
        x = max(0, (sw - w) // 2 + step)
        y = max(0, (sh - h) // 3 + step)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        # Pop in front of the browser, then behave like a normal window.
        self.attributes("-topmost", True)
        self.lift()
        self.after(400, lambda: self._safe(lambda: self.attributes("-topmost", False)))
        try:
            self.focus_force()
        except tk.TclError:
            pass

    @staticmethod
    def _safe(fn: Callable[[], Any]) -> None:
        try:
            fn()
        except tk.TclError:
            pass

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _set_buttons(self, items: list[tuple[str, Callable[[], Any], bool]]) -> None:
        for button in self._button_widgets:
            button.destroy()
        self._button_widgets = []
        self.unbind("<Return>")
        for text, command, default in reversed(items):
            button = ttk.Button(self.buttons, text=text, command=command, width=14,
                                default="active" if default else "normal")
            button.pack(side="right", padx=(GAP, 0))
            self._button_widgets.append(button)
            if default:
                self.bind("<Return>", lambda _e, c=command: c())

    # ------------------------------------------------------------------ step 1: ask
    def _build_prompt(self) -> None:
        self._mode = "prompt"
        self._clear()
        body = self.body
        body.grid_columnconfigure(1, weight=1)
        ttk.Label(body, text="Download this file?", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        info = " • ".join(part for part in (dl.format_size(self._size_bytes) or "Size unknown", self._source) if part)
        ttk.Label(body, text=info, style="Hint.TLabel", wraplength=520).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, PAD))
        ttk.Label(body, text="Save as:").grid(row=2, column=0, sticky="w")
        entry = ttk.Entry(body, textvariable=self.name_var, width=62)
        entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, GAP), padx=(GAP, 0))
        ttk.Label(body, text="Save to:").grid(row=3, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.folder_var, width=50).grid(
            row=3, column=1, sticky="ew", padx=(GAP, 0))
        ttk.Button(body, text="Browse…", command=self._browse).grid(row=3, column=2, padx=(GAP, 0))
        row = 4
        if self._ext:
            ttk.Checkbutton(body, text=f"Always save {self._ext} files in this folder",
                            variable=self.remember_ext).grid(row=row, column=1, columnspan=2, sticky="w", pady=(GAP, 0))
            row += 1
        if self._host:
            ttk.Checkbutton(body, text=f"Always save downloads from {self._host} in this folder",
                            variable=self.remember_site).grid(row=row, column=1, columnspan=2, sticky="w")
        self._set_buttons([
            ("Download", lambda: self._begin(False), True),
            ("Later", lambda: self._begin(True), False),
            ("Cancel", self._on_close, False),
        ])
        self.status.configure(text="")
        entry.focus_set()
        entry.select_range(0, tk.END)

    def _browse(self) -> None:
        start = self.folder_var.get()
        selected = filedialog.askdirectory(
            parent=self, title="Save to", initialdir=start if os.path.isdir(start) else None)
        if selected:
            self.folder_var.set(os.path.normpath(selected))

    def _begin(self, later: bool) -> None:
        name = " ".join(self.name_var.get().split())
        folder = self.folder_var.get().strip()
        if not name:
            self.status.configure(text="Type a file name.")
            return
        if not folder:
            self.status.configure(text="Choose where to save the file.")
            return
        if self._is_file and self._orig_ext and not os.path.splitext(name)[1]:
            name += self._orig_ext
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.status.configure(text=f"Cannot use that folder: {exc.strerror or exc}")
            return
        try:
            request_id = self._api.start(
                name, folder, later,
                {"ext": bool(self.remember_ext.get()), "site": bool(self.remember_site.get())})
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text=f"Could not start: {exc}"[:90])
            return
        if not request_id:
            self.status.configure(text="Could not add the download.")
            return
        self._request_id = request_id
        if later:
            # Kept in the app's list, paused; the user starts it from there.
            self._closed = True
            self.destroy()
            return
        self._build_progress(name, folder)
        self._tick()

    # ------------------------------------------------------------------ step 2: progress
    def _build_progress(self, name: str, folder: str) -> None:
        self._mode = "progress"
        self._clear()
        self.title("Downloading")
        body = self.body
        body.grid_columnconfigure(0, weight=1)
        self.name_label = ttk.Label(body, text=name, style="Title.TLabel", wraplength=520)
        self.name_label.grid(row=0, column=0, sticky="w")
        self.state_label = ttk.Label(body, text="Starting…")
        self.state_label.grid(row=1, column=0, sticky="w", pady=(2, GAP))
        self.bar = ttk.Progressbar(body, orient="horizontal", mode="determinate", maximum=100, length=520)
        self.bar.grid(row=2, column=0, sticky="ew")
        self.stats_label = ttk.Label(body, text="", style="Hint.TLabel", wraplength=520)
        self.stats_label.grid(row=3, column=0, sticky="w", pady=(GAP, 0))
        ttk.Label(body, text=f"Save to: {folder}", style="Hint.TLabel", wraplength=520).grid(
            row=4, column=0, sticky="w", pady=(2, 0))
        self._buttons_for("running")

    def _buttons_for(self, kind: str) -> None:
        if kind == self._last_status:
            return
        self._last_status = kind
        if kind == "running":
            self._set_buttons([("Pause", self._pause, False), ("Cancel", self._cancel, False),
                               ("Minimize", self.iconify, False)])
        elif kind == "paused":
            self._set_buttons([("Resume", self._resume, True), ("Cancel", self._cancel, False),
                               ("Minimize", self.iconify, False)])
        elif kind == "stopping":
            self._set_buttons([("Minimize", self.iconify, False)])
        elif kind == "done":
            self._set_buttons([("Open", self._open_file, True), ("Show in folder", self._open_folder, False),
                               ("Delete", self._delete_file, False), ("Close", self._on_close, False)])
        elif kind == "failed":
            self._set_buttons([("Retry", self._retry, True), ("Close", self._on_close, False)])
        else:  # cancelled, deleted, removed
            self._set_buttons([("Close", self._on_close, True)])

    # ------------------------------------------------------------------ step 3: finished
    def _build_done(self, size: str) -> None:
        import file_drag

        self._clear()
        self.title("Download complete")
        body = self.body
        body.grid_columnconfigure(0, weight=1)
        path = self._path
        ttk.Label(body, text="Download complete", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=" • ".join(p for p in (Path(path).name, size) if p), wraplength=520).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, GAP))
        ttk.Label(body, text="Saved in:", style="Hint.TLabel").grid(row=2, column=0, columnspan=2, sticky="w")
        self.path_var = tk.StringVar(self, value=path)
        entry = ttk.Entry(body, textvariable=self.path_var, state="readonly", width=60)
        entry.grid(row=3, column=0, sticky="ew")
        ttk.Button(body, text="Copy path", command=self._copy_path).grid(row=3, column=1, padx=(GAP, 0))
        if file_drag.available():
            handle = tk.Label(body, text="Drag this file into Telegram, WhatsApp, a browser or a folder",
                              relief="groove", bd=2, cursor="hand2", padx=PAD, pady=PAD,
                              bg=native.dialog_bg(self), fg=native.C["TEXT"])
            handle.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
            handle.bind("<B1-Motion>", self._drag_out)
        self._last_status = ""
        self._buttons_for("done")
        # A finished download comes to the front, even if the window was minimized or buried.
        self._safe(self.deiconify)
        self._safe(self.lift)
        self._safe(lambda: self.attributes("-topmost", True))
        self.after(600, lambda: self._safe(lambda: self.attributes("-topmost", False)))

    def _copy_path(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._path)
        self.update_idletasks()
        self._on_close()

    def _drag_out(self, _event: Any) -> str:
        import file_drag

        if self._path and os.path.exists(self._path):
            try:
                file_drag.drag_files(self.winfo_id(), [self._path])
            except Exception:
                pass
        return "break"

    def _tick(self) -> None:
        if self._closed:
            return
        try:
            self._refresh()
        except tk.TclError:
            return
        except Exception:
            pass
        if self._mode == "progress" and not self._closed:
            self._tick_job = self.after(TICK_MS, self._tick)

    def _refresh(self) -> None:
        snap, metrics = self._api.state(self._request_id)
        if snap is None:
            self.state_label.configure(text="This download was removed from the list.")
            self.bar.stop()
            self._buttons_for("removed")
            self._mode = "ended"
            return
        status = str(snap.get("status") or "")
        name = str(snap.get("name") or "")
        if name and name != self.name_label.cget("text"):
            self.name_label.configure(text=name)
        if self._mode != "progress":
            return
        percent = max(0.0, min(100.0, float(snap.get("progress") or 0.0) * 100.0))
        if status == "completed":
            self._path = str(snap.get("result_path") or "")
            self.bar.configure(value=100)
            size = ""
            try:
                size = dl.format_size(os.path.getsize(self._path))
            except OSError:
                pass
            self._build_done(size)
            self._mode = "ended"
            return
        if status == "failed":
            self.state_label.configure(text="The download failed.")
            self.stats_label.configure(text=str(snap.get("error") or snap.get("detail") or "")[:300])
            self.title("Download failed")
            self._buttons_for("failed")
            return
        if status == "cancelled":
            self.state_label.configure(text="Download cancelled.")
            self.title("Download cancelled")
            self._buttons_for("cancelled")
            self._mode = "ended"
            return
        if status == "paused":
            self.state_label.configure(text="Paused")
            self._buttons_for("paused")
            self.bar.configure(value=percent)
            return
        if status == "cancelling":
            self.state_label.configure(text=str(snap.get("detail") or "Stopping…"))
            self._buttons_for("stopping")
            return
        # queued / running
        self._buttons_for("running")
        detail = str(snap.get("detail") or "")
        self.state_label.configure(text=f"{percent:.0f}%  •  {detail}" if percent >= 1 and detail else
                                   (f"{percent:.0f}%" if percent >= 1 else (detail or "Starting…")))
        self.bar.configure(value=percent)
        total = int(metrics.get("total_bytes") or 0)
        done = int(metrics.get("downloaded_bytes") or 0)
        parts = []
        if done:
            parts.append(f"{dl.format_size(done)} of {dl.format_size(total)}" if total else dl.format_size(done))
        speed = dl.format_speed(metrics.get("speed_bps"))
        if speed:
            parts.append(speed)
        eta = dl.format_eta(metrics.get("eta_seconds"))
        if eta:
            parts.append(f"{eta} left")
        self.stats_label.configure(text="  •  ".join(parts))
        self.title(f"{percent:.0f}% - {name or 'Downloading'}")

    # ------------------------------------------------------------------ actions
    def _pause(self) -> None:
        self._api.pause(self._request_id)

    def _resume(self) -> None:
        self._api.resume(self._request_id)

    def _cancel(self) -> None:
        # Cancelling closes the window at once; the app finishes stopping in the background.
        self._api.cancel(self._request_id)
        self._on_close()

    def _retry(self) -> None:
        self._api.retry(self._request_id)

    def _open_file(self) -> None:
        if self._path and os.path.exists(self._path):
            open_path(self._path)
            self._on_close()
        else:
            messagebox.showwarning("Media Downloader", "The file is no longer on the disk.", parent=self)

    def _open_folder(self) -> None:
        reveal(self._path or self.folder_var.get())
        self._on_close()

    def _delete_file(self) -> None:
        if not self._path or not os.path.exists(self._path):
            messagebox.showwarning("Media Downloader", "The file is no longer on the disk.", parent=self)
            return
        if not messagebox.askyesno(
                "Delete file",
                f"Delete this file from the disk?\n\n{self._path}\n\nThis cannot be undone.",
                icon="warning", parent=self):
            return
        try:
            os.remove(self._path)
        except OSError as exc:
            messagebox.showerror("Media Downloader", f"Could not delete the file.\n\n{exc}", parent=self)
            return
        self._api.deleted(self._request_id, self._path)
        self._on_close()

    def _on_escape(self) -> None:
        if self._mode == "prompt":
            self._on_close()

    def _on_close(self) -> None:
        # Closing the window never stops a download that is running: it carries on in the list.
        self._closed = True
        if self._tick_job:
            try:
                self.after_cancel(self._tick_job)
            except tk.TclError:
                pass
        self._safe(self.destroy)
