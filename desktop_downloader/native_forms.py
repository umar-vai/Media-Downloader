"""Dialogs of the native (C++) desktop window.

Each dialog is described here as a small tree of boxes, grids, group boxes, tabs and controls
(see ``native_desktop/forms.h``). The C++ window draws it with real Windows controls and sends
back clicks and edits; the logic stays here, next to the controller, so every dialog behaves
exactly like the Tk dialog it replaces.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Any, Callable

import download_list as dl

PAD = 10
GAP = 6
_ids = itertools.count(1)


# ------------------------------------------------------------------------------ spec helpers
def label(text: str, style: str = "", *, wrap: bool = False, wrap_width: int = 0, **kw: Any) -> dict[str, Any]:
    node = {"type": "label", "text": str(text), "style": style, "wrap": wrap, **kw}
    if wrap_width:
        node["wrapWidth"] = wrap_width
    return node


def entry(wid: str, value: str, width: int = 20, *, readonly: bool = False, **kw: Any) -> dict[str, Any]:
    return {"type": "entry", "id": wid, "value": str(value), "width": width, "readonly": readonly, **kw}


def text_box(wid: str, value: str, *, width: int = 60, height: int = 5, readonly: bool = False, mono: bool = False,
             kind: str = "", wrap: bool = True, **kw: Any) -> dict[str, Any]:
    return {"type": "text", "id": wid, "value": str(value), "width": width, "height": height, "readonly": readonly,
            "mono": mono, "kind": kind, "wrap": wrap, **kw}


def check(wid: str, text: str, value: bool, **kw: Any) -> dict[str, Any]:
    return {"type": "check", "id": wid, "text": text, "value": bool(value), **kw}


def radio(wid: str, group: str, text: str, value: bool, **kw: Any) -> dict[str, Any]:
    return {"type": "radio", "id": wid, "group": group, "text": text, "value": bool(value), **kw}


def combo(wid: str, items: list[str], value: str, width: int = 16, **kw: Any) -> dict[str, Any]:
    return {"type": "combo", "id": wid, "items": [str(i) for i in items], "value": str(value), "width": width, **kw}


def button(wid: str, text: str, width: int = 11, *, enabled: bool = True, **kw: Any) -> dict[str, Any]:
    return {"type": "button", "id": wid, "text": text, "width": width, "enabled": enabled, **kw}


def progress(wid: str, value: float, *, busy: bool = False, length: int = 200, **kw: Any) -> dict[str, Any]:
    return {"type": "progress", "id": wid, "value": max(0.0, min(1.0, float(value or 0))), "busy": busy,
            "length": length, **kw}


def image(name: str, size: int = 32, **kw: Any) -> dict[str, Any]:
    return {"type": "image", "name": name, "size": size, **kw}


def picture(png_b64: str, width: int, height: int, placeholder: str = "No preview") -> dict[str, Any]:
    return {"type": "picture", "png": png_b64, "w": width, "h": height, "text": placeholder}


def group(title: str, body: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"type": "group", "title": title, "body": body, **kw}


def tabs(wid: str, pages: list[tuple[str, dict[str, Any]]], selected: int = 0) -> dict[str, Any]:
    return {"type": "tabs", "id": wid, "selected": selected, "pages": [{"title": t, "body": b} for t, b in pages]}


def vbox(*children: dict[str, Any] | None, gap: int = 0, **kw: Any) -> dict[str, Any]:
    return {"type": "vbox", "children": [c for c in children if c], "gap": gap, **kw}


def hbox(*children: dict[str, Any] | None, gap: int = GAP, **kw: Any) -> dict[str, Any]:
    return {"type": "hbox", "children": [c for c in children if c], "gap": gap, **kw}


def grid(columns: int, *children: dict[str, Any] | None, weights: list[int] | None = None, colgap: int = PAD,
         rowgap: int = 4, **kw: Any) -> dict[str, Any]:
    node = {"type": "grid", "columns": columns, "children": [c or {"type": "space"} for c in children],
            "colgap": colgap, "rowgap": rowgap, **kw}
    if weights:
        node["weights"] = weights
    return node


def space(width: int = 0, height: int = GAP) -> dict[str, Any]:
    return {"type": "space", "width": width, "height": height}


def table(wid: str, columns: list[tuple[str, int]], rows: list[list[str]], *, height: int = 8,
          selected: int = -1, **kw: Any) -> dict[str, Any]:
    return {"type": "list", "id": wid, "columns": [{"title": t, "width": w} for t, w in columns],
            "rows": [[str(c) for c in r] for r in rows], "height": height, "selected": selected, **kw}


def push(wid: str, text: str, *, default: bool = False, enabled: bool = True, width: int = 11) -> dict[str, Any]:
    return {"id": wid, "text": text, "default": default, "enabled": enabled, "width": width}


def pad(node: dict[str, Any], top: int = 0, bottom: int = 0, x: int = 0) -> dict[str, Any]:
    node = dict(node)
    if top:
        node["padTop"] = top
    if bottom:
        node["padBottom"] = bottom
    if x:
        node["padx"] = x
    return node


def span(node: dict[str, Any], columns: int) -> dict[str, Any]:
    return {**node, "colspan": columns}


def disabled(node: dict[str, Any], off: bool = True) -> dict[str, Any]:
    return {**node, "enabled": not off}


# ------------------------------------------------------------------------------------ forms
class NativeForm:
    """One dialog. ``body()`` and ``buttons()`` describe it; ``handle()`` reacts to the user."""

    title = "Media Downloader"
    width = 0
    resizable = False
    modal = False
    topmost = False
    owner = "main"

    def __init__(self, shell: Any) -> None:
        self.shell = shell
        self.app = shell.app
        self.form_id = f"{type(self).__name__}-{next(_ids)}"
        self.status = ""
        self.status_kind = ""
        self.result: Any = None
        self.done = False
        self.focus = ""
        self.offset = 0
        self.flash = False
        self._done_var: tk.BooleanVar | None = None

    def body(self) -> dict[str, Any]:
        raise NotImplementedError

    def buttons(self) -> list[dict[str, Any]]:
        return []

    def window_title(self) -> str:
        return self.title

    def spec(self) -> dict[str, Any]:
        return {
            "id": self.form_id, "title": self.window_title(), "width": self.width, "resizable": self.resizable,
            "modal": self.modal, "topmost": self.topmost, "owner": self.owner, "body": self.body(),
            "buttons": self.buttons(), "status": self.status, "statusKind": self.status_kind, "focus": self.focus,
            "offset": self.offset, "flash": self.flash, "minimize": self.owner == "none",
        }

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        if event == "close" or (event == "click" and widget in {"close", "cancel"}):
            self.close()

    def close(self, result: Any = None) -> None:
        if self.done:
            return
        self.done = True
        self.result = result
        self.shell.remove_form(self)
        if self._done_var is not None:
            try:
                self._done_var.set(True)
            except tk.TclError:
                pass


def run_modal(shell: Any, form: NativeForm) -> Any:
    """Shows ``form`` and waits (keeping the app responsive) until it closes."""
    form.modal = True
    var = tk.BooleanVar(shell.app, value=False)
    form._done_var = var
    shell.show_form(form)
    if not form.done:
        shell.app.wait_variable(var)
    return form.result


# ----------------------------------------------------------------------------- message box
class MessageForm(NativeForm):
    width = 360

    def __init__(self, shell: Any, title: str, message: str, icon: str, choices: list[tuple[str, str]],
                 default: str, detail: str = "") -> None:
        super().__init__(shell)
        self.title = title or "Media Downloader"
        self.message = message
        self.detail = detail
        self.icon = icon
        self.choices = choices
        self.default = default
        self.cancel_value = next((value for value, _text in choices if value in {"cancel", "no"}), choices[-1][0])

    def body(self) -> dict[str, Any]:
        text = vbox(label(self.message, wrap=True, wrap_width=420),
                    pad(label(self.detail, "hint", wrap=True, wrap_width=420), top=GAP) if self.detail else None)
        return hbox(image(self.icon, 32, valign="top"), {**text, "stretch": True}, gap=PAD + 4, valign="top")

    def buttons(self) -> list[dict[str, Any]]:
        return [push(value, text, default=value == self.default) for value, text in self.choices]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        if event == "close":
            self.close(self.cancel_value)
        elif event == "click":
            self.close(widget)


def message_box(shell: Any, kind: str, title: str | None, message: str | None, **options: Any) -> Any:
    icon = str(options.get("icon") or {"showerror": "error", "showwarning": "warning", "showinfo": "info"}.get(
        kind, "question" if kind.startswith("ask") else "info"))
    icon = {"warning": "warning", "error": "error", "info": "info", "question": "question"}.get(icon, "info")
    choices = {
        "askyesno": [("yes", "Yes"), ("no", "No")],
        "askquestion": [("yes", "Yes"), ("no", "No")],
        "askokcancel": [("ok", "OK"), ("cancel", "Cancel")],
        "askretrycancel": [("retry", "Retry"), ("cancel", "Cancel")],
        "askyesnocancel": [("yes", "Yes"), ("no", "No"), ("cancel", "Cancel")],
    }.get(kind, [("ok", "OK")])
    default = str(options.get("default") or choices[0][0])
    form = MessageForm(shell, str(title or ""), str(message or ""), icon, choices, default, str(options.get("detail") or ""))
    answer = run_modal(shell, form)
    if kind in {"askyesno", "askokcancel", "askretrycancel"}:
        return answer in {"yes", "ok", "retry"}
    if kind == "askyesnocancel":
        return None if answer == "cancel" else answer == "yes"
    if kind == "askquestion":
        return "yes" if answer == "yes" else "no"
    return "ok"


def install_dialog_hooks(shell: Any) -> Callable[[], None]:
    """While the native window is up, Tk message boxes and file pickers open as native dialogs."""
    from tkinter import filedialog, messagebox

    saved: dict[tuple[Any, str], Any] = {}

    def patch(module: Any, name: str, fn: Callable[..., Any]) -> None:
        saved[(module, name)] = getattr(module, name)
        setattr(module, name, fn)

    for kind in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askyesnocancel",
                 "askquestion", "askretrycancel"):
        def make(kind: str = kind) -> Callable[..., Any]:
            def call(title: str | None = None, message: str | None = None, **options: Any) -> Any:
                if shell.closed:
                    return saved[(messagebox, kind)](title, message, **options)
                return message_box(shell, kind, title, message, **options)
            return call
        patch(messagebox, kind, make())

    def file_call(kind: str) -> Callable[..., Any]:
        def call(**options: Any) -> Any:
            name = {"open": "askopenfilename", "open_many": "askopenfilenames", "save": "asksaveasfilename",
                    "folder": "askdirectory"}[kind]
            if shell.closed:
                return saved[(filedialog, name)](**options)
            paths = shell.file_dialog(kind, options)
            if kind == "open_many":
                return tuple(paths)
            return paths[0] if paths else ""
        return call

    patch(filedialog, "askopenfilename", file_call("open"))
    patch(filedialog, "askopenfilenames", file_call("open_many"))
    patch(filedialog, "asksaveasfilename", file_call("save"))
    patch(filedialog, "askdirectory", file_call("folder"))

    def restore() -> None:
        for (module, name), fn in saved.items():
            setattr(module, name, fn)

    return restore


# ----------------------------------------------------------------------------- confirm delete
class ConfirmDeleteForm(NativeForm):
    width = 420

    def __init__(self, shell: Any, count: int, files: int, finished_only: bool) -> None:
        super().__init__(shell)
        self.title = "Delete completed" if finished_only else "Delete downloads"
        noun = f"{count} download{'s' if count != 1 else ''}"
        self.text = f"Remove {noun} that finished from the list?" if finished_only else f"Remove {noun} from the list?"
        self.files = files
        self.delete_files = False

    def body(self) -> dict[str, Any]:
        box = check("files", f"Also delete {self.files} downloaded file{'s' if self.files != 1 else ''} from the disk "
                    "(cannot be undone)", self.delete_files, enabled=bool(self.files))
        return hbox(image("warning", 32, valign="top"), {**vbox(
            label(self.text, "title"),
            pad(label("Unfinished downloads are stopped first and their partial files are deleted. Finished files "
                      "stay on the disk unless you tick the box below.", wrap=True, wrap_width=380), top=GAP,
                bottom=PAD),
            box), "stretch": True}, gap=PAD, valign="top")

    def buttons(self) -> list[dict[str, Any]]:
        return [push("delete", "Delete", default=True), push("cancel", "Cancel")]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        self.delete_files = bool(values.get("files"))
        if event == "click" and widget == "delete":
            self.close((True, self.delete_files and self.files > 0))
        elif event == "close" or widget == "cancel":
            self.close((False, False))


# --------------------------------------------------------------------------------- Add URL
class AddUrlForm(NativeForm):
    title = "Add URL"
    width = 620

    def body(self) -> dict[str, Any]:
        app = self.app
        running = bool(app.analysis_running)
        is_video = app.mode_var.get() == "Video"
        info = vbox(
            label(str(app.media_badge.cget("text")), "hint"),
            pad(label(str(app.title_label.cget("text")), "title", wrap=True, wrap_width=420), top=2, bottom=2),
            label(str(app.meta_label.cget("text")), wrap=True, wrap_width=420),
            pad(label(str(app.media_action_hint.cget("text")), "hint", wrap=True, wrap_width=420), top=GAP)
            if str(app.media_action_hint.cget("text")) else None,
        )
        thumb = picture(self.shell.add_thumbnail, 184, 104)
        save = grid(
            4,
            label("Type:"), span(hbox(radio("video", "mode", "Video", is_video), radio("audio", "mode", "Audio", not is_video),
                                      gap=PAD * 2), 3),
            label("Video quality:", "" if is_video else "faint"),
            span(combo("quality", ["Best available", "1080p", "720p", "480p", "360p"], app.video_quality_var.get(),
                       16, enabled=is_video, align="left"), 3),
            label("Audio format:", "faint" if is_video else ""),
            span(hbox(combo("format", ["MP3", "M4A", "FLAC", "OPUS", "WAV"], app.audio_format_var.get(), 8,
                            enabled=not is_video),
                      pad(label("Bitrate (kbps):", "faint" if is_video else ""), x=0),
                      combo("bitrate", ["128", "192", "256", "320"], app.audio_quality_var.get(), 6, enabled=not is_video),
                      gap=GAP), 3),
            label("File name:"), span(entry("name", app.name_var.get(), 40), 3),
            label("Save to:"), entry("folder", str(app.download_dir), 30, readonly=True),
            button("browse", "Browse…", 10), button("default", "Default", 9),
            None, span(label(str(app.output_hint.cget("text")), "hint"), 3),
            weights=[0, 1, 0, 0], colgap=GAP,
        )
        return vbox(
            grid(3,
                 label("Address:"), entry("url", app.url_var.get(), 62, enter=True),
                 hbox(button("paste", "Paste", 9), button("analyze", "Analyze", 11, enabled=not running),
                      button("stop", "Stop", 9, enabled=running), gap=GAP),
                 None, span(label("Paste a link to a video, audio or file. It is read automatically.", "hint"), 2),
                 weights=[0, 1, 0], colgap=GAP),
            pad(group("File information", hbox(thumb, {**info, "stretch": True}, gap=PAD, valign="top")), top=GAP),
            pad(group("Save as", save), top=PAD),
            pad(check("edit", "Open in the media editor after downloading", app.open_editor_after_var.get()), top=PAD),
        )

    def buttons(self) -> list[dict[str, Any]]:
        app = self.app
        return [push("download", str(app.download_button.cget("text")), default=True,
                     enabled=not app.download_button.instate(["disabled"]), width=16),
                push("cancel", "Cancel")]

    def spec(self) -> dict[str, Any]:
        self.status = str(self.app.status_label.cget("text"))
        return super().spec()

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        app = self.app
        url = str(values.get("url") or "")[:8192]
        if url != app.url_var.get():
            app.url_var.set(url)
            app._on_url_changed()
        if str(values.get("name") or "") != app.name_var.get():
            app.name_var.set(str(values.get("name") or "")[:350])
        for key, var, choices in (("quality", app.video_quality_var, app.video_quality),
                                  ("format", app.audio_format_var, app.audio_format),
                                  ("bitrate", app.audio_quality_var, app.audio_quality)):
            value = values.get(key)
            if isinstance(value, str) and value and value in choices.cget("values") and value != var.get():
                var.set(value)
        app.open_editor_after_var.set(bool(values.get("edit")))
        if widget in {"video", "audio"} and event == "change":
            app._sync_mode("Audio" if values.get("audio") else "Video")
        if event == "close" or widget == "cancel":
            self.shell.close_add()
        elif event == "enter" or widget == "analyze":
            app.analyze_media()
        elif widget == "paste":
            app.paste_from_clipboard()
        elif widget == "stop":
            app.cancel_analysis()
        elif widget == "browse":
            app.choose_download_folder()
        elif widget == "default":
            app.reset_download_folder()
        elif widget == "download":
            app.download()


# ---------------------------------------------------------------------------------- Batch
class BatchForm(NativeForm):
    title = "Add batch download"
    resizable = True

    def __init__(self, shell: Any) -> None:
        super().__init__(shell)
        page = self.app.batch_page
        self.links = ""
        self.mode = page.mode_var.get()
        self.quality = page.quality_var.get()
        self.format = page.audio_format_var.get()
        self.bitrate = page.audio_quality_var.get()
        self.status = "Each batch shows up in the list under Queues."
        self.focus = "links"
        self._links_rev = 0

    def _count(self) -> tuple[str, str]:
        from batch_download import MAX_BATCH_URLS, parse_batch_urls_detailed

        count, dupes, bad = 0, 0, 0
        try:
            urls, dupes, bad = parse_batch_urls_detailed(self.links)[0:3]
            count = len(urls)
        except ValueError as exc:
            if "up to" in str(exc):
                count = MAX_BATCH_URLS + 1
        extra = f" • {dupes} duplicate" if dupes else ""
        extra += f" • {bad} invalid" if bad else ""
        return f"{count} / {MAX_BATCH_URLS} links{extra}", "error" if count > MAX_BATCH_URLS else "hint"

    def body(self) -> dict[str, Any]:
        audio = self.mode == "Audio"
        count, style = self._count()
        return vbox(
            hbox({**label("Links to download (one per line, up to 300):"), "stretch": True}, label(count, style)),
            pad(text_box("links", self.links, width=80, height=14, mono=True, wrap=False, force=self._links_rev > 0,
                         rev=self._links_rev), top=GAP),
            pad(hbox(button("paste", "Paste", 9), button("load", "Load from file…", 15), button("clear", "Clear", 9)),
                top=GAP, ),
            pad(group("Save as", grid(
                4,
                label("Type:"), span(hbox(radio("video", "bmode", "Video", not audio), radio("audio", "bmode", "Audio", audio),
                                          gap=PAD * 2), 3),
                label("Video quality:"), span(combo("quality", ["Best available", "1080p", "720p", "480p", "360p"],
                                                    self.quality, 16, enabled=not audio), 3),
                label("Audio format:"), combo("format", ["MP3", "M4A", "FLAC", "OPUS", "WAV"], self.format, 8, enabled=audio),
                label("Bitrate (kbps):"), combo("bitrate", ["128", "192", "256", "320"], self.bitrate, 6, enabled=audio),
                weights=[0, 0, 0, 1])), top=PAD),
        )

    def buttons(self) -> list[dict[str, Any]]:
        return [push("queue", "Add to queue", default=True, width=14), push("close", "Close")]

    def _set_links(self, text: str) -> None:
        self.links = text[:2_000_000]
        self._links_rev += 1

    def _append(self, value: str) -> None:
        existing = self.links
        if existing and not existing.endswith("\n"):
            existing += "\n"
        self._set_links(existing + value.strip() + "\n")

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        from tkinter import filedialog, messagebox

        from batch_download import parse_batch_urls_detailed

        if isinstance(values.get("links"), str):
            self.links = values["links"][:2_000_000]
        self.mode = "Audio" if values.get("audio") else "Video"
        for key in ("quality", "format", "bitrate"):
            if isinstance(values.get(key), str) and values[key]:
                setattr(self, key, values[key])
        if event == "close" or widget == "close":
            self.shell.close_batch()
        elif widget == "paste":
            try:
                self._append(self.app.clipboard_get())
            except Exception:
                pass
        elif widget == "clear":
            self._set_links("")
        elif widget == "load":
            path = filedialog.askopenfilename(title="Load links from a text file",
                                              filetypes=[("Text files", "*.txt *.csv *.list *.md"), ("All files", "*.*")])
            if path:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        self._append(handle.read(2_000_000))
                except OSError as exc:
                    messagebox.showerror("Batch download", f"Could not read the file. {exc}")
        elif widget == "queue":
            try:
                urls, duplicates, invalid = parse_batch_urls_detailed(self.links)
            except ValueError as exc:
                messagebox.showerror("Batch download", str(exc))
                return
            try:
                batch_id, queued, _dupes = self.app._enqueue_batch_downloads(
                    "\n".join(urls), {"mode": self.mode, "video_quality": self.quality,
                                      "audio_format": self.format, "audio_quality": self.bitrate})
            except (ValueError, OSError) as exc:
                messagebox.showerror("Batch download", str(exc))
                return
            note = f"Queued {queued} link{'s' if queued != 1 else ''}"
            if duplicates:
                note += f" • {duplicates} duplicate skipped"
            if invalid:
                note += f" • {invalid} invalid skipped"
            self.status = note
            self._set_links("")
            self.app._after_batch_queued(batch_id)
            skipped = f" · {duplicates} duplicate skipped" if duplicates else ""
            self.app._set_status(f"Queued {queued} batch downloads{skipped}", "ready")


# -------------------------------------------------------------------------- Updates & about
class UpdatesForm(NativeForm):
    title = "Updates & about"
    width = 500

    def body(self) -> dict[str, Any]:
        from version import APP_VERSION

        app = self.app
        def off(widget: Any) -> bool:
            return bool(widget.instate(["disabled"]))
        updates = vbox(
            hbox({**label(str(app.update_status_label.cget("text")), "title"), "stretch": True},
                 button("check", "Check for updates", 18, enabled=not off(app.update_check_button))),
            pad(label(str(app.update_detail_label.cget("text")), wrap=True, wrap_width=460), top=GAP),
            pad(label(str(app.update_notes_label.cget("text")), "hint", wrap=True, wrap_width=460), top=GAP)
            if str(app.update_notes_label.cget("text")) else None,
            pad(progress("progress", float(app.update_progress.cget("value")) / 1000.0, length=460), top=PAD),
            pad(hbox(button("install", str(app.update_action_button.cget("text")), 14, enabled=not off(app.update_action_button)),
                     button("later", "Later", 9, enabled=not off(app.update_later_button)),
                     button("cancel_download", "Cancel download", 15, enabled=not off(app.update_cancel_button)),
                     button("release", str(app.release_button.cget("text")), 18)), top=PAD),
        )
        about = vbox(
            label(f"Saving to  {app.download_dir}", wrap=True, wrap_width=460),
            pad(label("Use only for content you own or have permission to download.", "hint"), top=2, bottom=PAD),
            hbox(button("diagnostics", "Diagnostics…", 14), button("developer", "Developer website", 18)),
        )
        return vbox(
            hbox(image("app", 40, valign="top"),
                 vbox(label("Media Downloader", "big"), label(f"Version {APP_VERSION}", "hint")), gap=PAD, valign="top"),
            pad(group("Updates", updates), top=PAD),
            pad(group("About", about), top=PAD),
        )

    def buttons(self) -> list[dict[str, Any]]:
        return [push("close", "Close", default=True)]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        app = self.app
        if event == "close" or widget == "close":
            self.shell.close_updates()
        elif widget == "check":
            app.check_for_updates(manual=True)
        elif widget == "install" and not app.update_action_button.instate(["disabled"]):
            app.download_or_install_update()
        elif widget == "later":
            app.snooze_update()
            self.shell.close_updates()
        elif widget == "cancel_download":
            app.cancel_update_download()
        elif widget == "release":
            app.release_button.invoke()
        elif widget == "diagnostics":
            app.open_diagnostics()
        elif widget == "developer":
            webbrowser.open("https://github.com/umar-vai")


# --------------------------------------------------------------------------------- Options
SITE_QUALITIES = ["Default", "Best available", "1080p", "720p", "480p", "360p"]
OPTION_TABS = ("General", "Downloads", "Save To", "Files", "Browser", "Updates")
OPTION_DEFAULTS: dict[str, Any] = {
    "download_dir": "", "default_mode": "Video", "video_quality": "720p", "audio_format": "MP3",
    "audio_quality": "192", "max_concurrent_downloads": 6, "auto_analyze_links": True,
    "open_editor_after_download": False, "confirm_before_exit": True, "close_to_tray": True, "capture_dialog": True,
    "start_with_windows": False, "auto_check_updates": True, "auto_download_updates": False, "theme": "Light",
    "skip_existing": True, "organize_by_site": False, "fast_mode": True, "embed_metadata": True,
    "embed_thumbnail": False, "subtitles": "Off", "subtitle_langs": "en", "clipboard_hotkey": True,
    "extension_enabled": True, "cookies_file": "", "cookies_browser": "None",
}


class SiteRuleForm(NativeForm):
    title = "Website folder"

    def __init__(self, shell: Any, host: str = "", folder: str = "", quality: str = "Default") -> None:
        super().__init__(shell)
        self.host = host
        self.folder = folder
        self.quality = quality if quality in SITE_QUALITIES else "Default"
        self.focus = "host"

    def body(self) -> dict[str, Any]:
        return vbox(
            pad(label("Downloads from this website are saved in their own folder."), bottom=PAD),
            grid(3,
                 label("Website:"), span(entry("host", self.host, 44), 2),
                 None, span(label("e.g. youtube.com (also covers m.youtube.com)", "hint"), 2),
                 label("Folder:"), entry("folder", self.folder, 36, force=True), button("browse", "Browse…", 10),
                 label("Quality:"), span(combo("quality", SITE_QUALITIES, self.quality, 18), 2),
                 weights=[0, 1, 0], colgap=GAP, rowgap=GAP),
        )

    def buttons(self) -> list[dict[str, Any]]:
        return [push("ok", "OK", default=True), push("cancel", "Cancel")]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        from tkinter import filedialog, messagebox

        self.host = str(values.get("host", self.host))
        self.folder = str(values.get("folder", self.folder))
        self.quality = str(values.get("quality") or self.quality)
        if event == "close" or widget == "cancel":
            self.close(None)
        elif widget == "browse":
            current = Path(self.folder).expanduser()
            chosen = filedialog.askdirectory(title="Choose the folder for this website",
                                             initialdir=str(current if current.exists() else Path.home()))
            if chosen:
                self.folder = os.path.normpath(chosen)
        elif widget == "ok":
            if not self.host.strip():
                messagebox.showerror("Website folder", "Enter the website, for example youtube.com.")
                return
            if not self.folder.strip() and self.quality == "Default":
                messagebox.showerror("Website folder", "Choose a folder or a quality for this website.")
                return
            self.close((self.host.strip(), self.folder.strip(), self.quality))


class OptionsForm(NativeForm):
    title = "Options"

    def __init__(self, shell: Any, tab: str | None = None) -> None:
        from download_rules import parse_rules

        super().__init__(shell)
        settings = self.app.settings
        self.values: dict[str, Any] = {}
        for key, default in OPTION_DEFAULTS.items():
            value = settings.get(key, default)
            self.values[key] = bool(value) if isinstance(default, bool) else str(value if value not in (None, "") else default)
        if self.values["theme"] not in ("Light", "Dark"):
            self.values["theme"] = "Light"
        self.rules = [[r.host, r.folder, r.quality or "Default"] for r in parse_rules(str(settings.get("folder_rules") or ""))]
        self.selected_rule = -1
        self.dirty = False
        self.tab = 0
        self.select_tab(tab)

    def select_tab(self, name: str | None) -> None:
        name = {"Website folders": "Save To", "About": "Updates"}.get(name or "", name or "")
        if name in OPTION_TABS:
            self.tab = OPTION_TABS.index(name)

    def _c(self, key: str, text: str) -> dict[str, Any]:
        return check(key, text, bool(self.values[key]))

    def body(self) -> dict[str, Any]:
        from install_mode import install_mode_name
        from version import APP_VERSION

        v = self.values
        app = self.app
        general = vbox(
            group("Startup and exit", vbox(
                self._c("start_with_windows", "Start Media Downloader when Windows starts"),
                self._c("close_to_tray", "Keep running in the notification area when the window is closed"),
                self._c("confirm_before_exit", "Ask before closing while downloads are running"))),
            pad(group("Links", vbox(
                self._c("auto_analyze_links", "Read a link's title and formats as soon as it is pasted"),
                self._c("open_editor_after_download", "Open finished downloads in the media editor"),
                self._c("clipboard_hotkey", "Global hotkeys: Ctrl+Alt+D downloads the copied link, Ctrl+Alt+M its audio"),
                self._c("capture_dialog", "Ask where to save (with a progress window) for downloads started in the browser"))),
                top=PAD),
            pad(group("Appearance", hbox(label("Theme:"), combo("theme", ["Light", "Dark"], v["theme"], 12),
                                         pad(label("Used the next time the app starts.", "hint"), x=0), gap=GAP)), top=PAD),
        )
        downloads = vbox(
            group("Default download folder", hbox({**entry("download_dir", v["download_dir"], 50), "stretch": True},
                                                  button("browse_dir", "Browse…", 10))),
            pad(group("Default format", grid(
                2,
                label("Download type:"), combo("default_mode", ["Video", "Audio"], v["default_mode"]),
                label("Video quality:"), combo("video_quality", ["Best available", "1080p", "720p", "480p", "360p"], v["video_quality"]),
                label("Audio format:"), combo("audio_format", ["MP3", "M4A", "FLAC", "OPUS", "WAV"], v["audio_format"]),
                label("Audio bitrate (kbps):"), combo("audio_quality", ["320", "256", "192", "128"], v["audio_quality"]),
                weights=[0, 1])), top=PAD),
            pad(group("Connections", vbox(
                hbox(label("Maximum simultaneous downloads:"), {"type": "spin", "id": "max_concurrent_downloads",
                                                                "value": str(v["max_concurrent_downloads"]), "min": 1,
                                                                "max": 10, "width": 5}, gap=PAD),
                pad(label("Default 6. More can trigger rate limits on some sites.", "hint"), top=4))), top=PAD),
        )
        rows = [[h, f, q] for h, f, q in self.rules]
        save_to = vbox(
            pad(label("Downloads from these websites are saved in their own folder (and quality):", wrap=True,
                      wrap_width=480), bottom=GAP),
            hbox({**table("sites", [("Website", 130), ("Folder", 250), ("Quality", 90)], rows, height=8,
                          selected=self.selected_rule), "stretch": True},
                 vbox(button("add_site", "Add…"), pad(button("edit_site", "Edit…"), top=GAP),
                      pad(button("remove_site", "Remove"), top=GAP)), gap=PAD, valign="top"),
            pad(self._c("organize_by_site", "Put other downloads in a sub-folder named after the website"), top=PAD),
        )
        files = vbox(
            group("Saving", vbox(self._c("skip_existing", "Skip files that are already in the folder"),
                                 self._c("fast_mode", "Fast mode: download stream pieces in parallel"))),
            pad(group("Metadata", vbox(self._c("embed_metadata", "Write the title, artist and chapters into the file"),
                                       self._c("embed_thumbnail", "Add the video thumbnail as cover art"))), top=PAD),
            pad(group("Subtitles (video)", grid(
                3,
                label("Subtitles:"), span(combo("subtitles", ["Off", "Sidecar file", "Embed in video"], v["subtitles"]), 2),
                label("Languages:"), entry("subtitle_langs", v["subtitle_langs"], 20),
                label("Comma separated, e.g. en,bn", "hint"), weights=[0, 0, 1])), top=PAD),
        )
        status = str(app.statusbar.extension.cget("text"))
        browser = vbox(
            group("Browser extension", vbox(
                self._c("extension_enabled", "Catch downloads started in the browser (Chrome, Edge, Brave, Firefox)"),
                pad(label(status, "ok" if "connected" in status.lower() else "hint"), top=2, bottom=GAP),
                label("Files you download in the browser are downloaded by Media Downloader, and the right-click menu "
                      "gets \"Download link with Media Downloader\". Hold Alt while clicking a link to let the browser "
                      "keep that download. The extension pairs itself; no token to copy.", wrap=True, wrap_width=470),
                pad(button("install_extension", "Install browser extension…", 24, align="left"), top=PAD))),
            pad(group("Pairing token (only for manual installs)", hbox(
                {**entry("token", str(app.settings.get("extension_token") or ""), 40, readonly=True), "stretch": True},
                button("copy_token", "Copy", 9))), top=PAD),
            pad(group("Cookies for logged-in sites", grid(
                3,
                label("cookies.txt file:"), entry("cookies_file", v["cookies_file"], 30), button("browse_cookies", "Browse…", 10),
                label("Or read from:"), span(combo("cookies_browser", ["None", "Firefox", "Chrome", "Edge", "Brave"],
                                                   v["cookies_browser"], 14), 2),
                weights=[0, 1, 0], colgap=GAP)), top=PAD),
        )
        updates = vbox(
            group("Automatic updates", vbox(
                self._c("auto_check_updates", "Check for updates when Media Downloader starts"),
                self._c("auto_download_updates", "Download verified updates automatically"),
                pad(hbox(button("check_now", "Check now", 11), button("release_notes", "Release notes", 13)), top=PAD))),
            pad(group("About", vbox(
                label(f"Media Downloader {APP_VERSION}", "title"),
                label(f"{install_mode_name().capitalize()} installation", "hint"),
                pad(label("Use only for content you own or have permission to download.", "hint"), top=GAP),
                pad(hbox(button("diagnostics", "Diagnostics…", 13), button("open_log", "Open log file", 13)), top=PAD))),
                top=PAD),
        )
        pages = [("General", general), ("Downloads", downloads), ("Save To", save_to), ("Files", files),
                 ("Browser", browser), ("Updates", updates)]
        return tabs("tabs", pages, self.tab)

    def buttons(self) -> list[dict[str, Any]]:
        return [push("ok", "OK", default=True), push("cancel", "Cancel"), push("apply", "Apply", enabled=self.dirty)]

    def _read(self, values: dict[str, Any]) -> None:
        for key, default in OPTION_DEFAULTS.items():
            if key in values:
                new = bool(values[key]) if isinstance(default, bool) else str(values[key])
                if new != self.values.get(key):
                    self.values[key] = new
                    self.dirty = True
        if isinstance(values.get("tabs"), int):
            self.tab = values["tabs"]
        if isinstance(values.get("sites"), int):
            self.selected_rule = values["sites"]

    def payload(self) -> dict[str, Any]:
        from options_dialog import rules_from_rows

        out = dict(self.values)
        try:
            out["max_concurrent_downloads"] = max(1, min(10, int(str(out["max_concurrent_downloads"]).strip() or 6)))
        except ValueError:
            out["max_concurrent_downloads"] = 6
        out["folder_rules"] = rules_from_rows([tuple(r) for r in self.rules])  # type: ignore[misc]
        return out

    def _save(self) -> bool:
        from tkinter import messagebox

        try:
            self.app._apply_settings(self.payload())
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Options", str(exc))
            return False
        self.dirty = False
        return True

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        from tkinter import filedialog

        self._read(values)
        app = self.app
        if event == "close" or widget == "cancel":
            self.shell.close_options()
        elif widget == "ok":
            if not self.dirty or self._save():
                self.shell.close_options()
        elif widget == "apply":
            self._save()
        elif widget == "browse_dir":
            current = Path(str(self.values["download_dir"])).expanduser()
            chosen = filedialog.askdirectory(title="Choose download folder", mustexist=True,
                                             initialdir=str(current if current.exists() else Path.home()))
            if chosen:
                self.values["download_dir"] = os.path.normpath(chosen)
                self.dirty = True
        elif widget == "browse_cookies":
            chosen = filedialog.askopenfilename(title="Choose cookies.txt",
                                                filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
            if chosen:
                self.values["cookies_file"] = os.path.normpath(chosen)
                self.dirty = True
        elif widget == "copy_token":
            app._copy_text(str(app.settings.get("extension_token") or ""), "pairing token")
            self.status = "Pairing token copied."
        elif widget == "install_extension":
            app.export_browser_extension()
        elif widget == "check_now":
            app.check_for_updates(manual=True)
        elif widget == "release_notes":
            app.open_release_page()
        elif widget == "diagnostics":
            app.open_diagnostics()
        elif widget == "open_log":
            app.open_app_log()
        elif widget == "add_site":
            result = run_modal(self.shell, SiteRuleForm(self.shell))
            if result:
                self.rules.append(list(result))
                self.selected_rule = len(self.rules) - 1
                self.dirty = True
        elif widget == "edit_site" or (event == "dblclick" and widget == "sites"):
            if 0 <= self.selected_rule < len(self.rules):
                result = run_modal(self.shell, SiteRuleForm(self.shell, *self.rules[self.selected_rule]))
                if result:
                    self.rules[self.selected_rule] = list(result)
                    self.dirty = True
        elif widget == "remove_site" or (event == "delete" and widget == "sites"):
            if 0 <= self.selected_rule < len(self.rules):
                del self.rules[self.selected_rule]
                self.selected_rule = min(self.selected_rule, len(self.rules) - 1)
                self.dirty = True


# ------------------------------------------------------------------------------- Properties
class PropertiesForm(NativeForm):
    width = 560

    def __init__(self, shell: Any, item: dict[str, Any], on_action: Callable[[str], None]) -> None:
        from history_window import entry_state

        super().__init__(shell)
        self.item = item
        self.on_action = on_action
        self.state = entry_state(item)
        self.tab = 0

    def window_title(self) -> str:
        return f"{self.item.get('file_name') or self.item.get('title') or 'Download'} Properties"

    def body(self) -> dict[str, Any]:
        from history_window import _MEDIA_ROWS, property_rows

        rows = property_rows(self.item)
        general = [row for row in rows if row[0] not in _MEDIA_ROWS]
        media = [row for row in rows if row[0] in _MEDIA_ROWS]
        pages = []
        for title, part in (("General", general), ("Media", media)):
            if not part:
                continue
            cells: list[dict[str, Any]] = []
            for index, (name, value) in enumerate(part):
                cells.append(label(f"{name}:", valign="top" if name == "Error" else "center"))
                if name == "Error":
                    cells.append(text_box(f"v{index}", value, width=58, height=5, readonly=True, kind="error"))
                else:
                    cells.append(entry(f"v{index}", value, 60, readonly=True))
            pages.append((title, grid(2, *cells, weights=[0, 1], rowgap=2)))
        bad = self.state in {"Failed", "Missing"}
        return vbox(
            label(str(self.item.get("title") or self.item.get("file_name") or "Download"), "title", wrap=True,
                  wrap_width=520),
            pad(label(self.state, "error" if bad else ""), bottom=PAD),
            tabs("tabs", pages, self.tab),
        )

    def buttons(self) -> list[dict[str, Any]]:
        item = self.item
        resumable = self.state in {"Failed", "Cancelled"}
        return [
            push("open", "Open", default=self.state == "Completed", enabled=self.state == "Completed"),
            push("reveal", "Open folder", enabled=bool(item.get("file_path") or item.get("download_dir"))),
            push("page", "Open web page", width=15, enabled=bool(item.get("page_url") or item.get("source_url"))),
            push("resume" if resumable else "redownload", "Resume" if resumable else "Download again", width=15),
            push("close", "Close"),
        ]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        if isinstance(values.get("tabs"), int):
            self.tab = values["tabs"]
        if event == "close" or widget == "close":
            self.close()
        elif event == "click" and widget in {"open", "reveal", "page", "resume", "redownload"}:
            self.on_action(widget)
            if widget in {"resume", "redownload"}:
                self.close()


# ----------------------------------------------------------------------------- Error details
class FailureForm(NativeForm):
    title = "Download error"
    resizable = True

    def __init__(self, shell: Any, item: dict[str, Any], on_retry: Callable[[str, str], None]) -> None:
        super().__init__(shell)
        self.item = item
        self.on_retry = on_retry
        self.url = str(item.get("url") or "")

    def body(self) -> dict[str, Any]:
        item = self.item
        attempts = int(item.get("auto_attempts") or 0)
        status = str(item.get("status") or "")
        sub = ("The download failed." if status == "failed"
               else f"Retrying automatically (attempt {attempts})." if attempts else status.title())
        error = str(item.get("last_error") or item.get("error") or "No error details were recorded.")
        return vbox(
            hbox(image("error", 32, valign="top"),
                 vbox(label(str(item.get("name") or "Download"), "title", wrap=True, wrap_width=520), label(sub, "hint")),
                 gap=PAD, valign="top"),
            pad(grid(2, label("Address:"), entry("url", self.url, 70),
                     None, label("Fix the address here if it changed, then press Retry.", "hint"),
                     weights=[0, 1], rowgap=2), top=PAD, bottom=GAP),
            group("Error details", text_box("error", error, width=80, height=10, readonly=True, mono=True)),
        )

    def buttons(self) -> list[dict[str, Any]]:
        return [push("retry", "Retry", default=True), push("copy", "Copy error"), push("close", "Close")]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        self.url = str(values.get("url", self.url))
        if event == "close" or widget == "close":
            self.close()
        elif widget == "copy":
            text = f"{self.url}\n\n{self.item.get('last_error') or self.item.get('error') or ''}"
            self.app.clipboard_clear()
            self.app.clipboard_append(text)
            self.status = "Copied to the clipboard."
        elif widget == "retry":
            self.on_retry(str(self.item.get("id") or ""), self.url.strip())
            self.close()


# ---------------------------------------------------------------------------- Diagnostics
class DiagnosticsForm(NativeForm):
    title = "Diagnostics"
    resizable = True

    def __init__(self, shell: Any) -> None:
        super().__init__(shell)
        self.report: dict[str, Any] | None = None
        self.checking = False
        self.summary = "Running checks…"
        self.summary_bad = False
        self.selected = -1
        self._result: dict[str, Any] | None = None
        self.app.after(250, self.run_checks)

    def run_checks(self) -> None:
        from self_test import run_self_test
        from version import APP_VERSION

        if self.checking or self.done:
            return
        self.checking = True
        self.status = ""
        self.summary = "Checking FFmpeg, mpv, updater, app mode and writable folders…"
        self.summary_bad = False

        def work() -> None:
            try:
                self._result = run_self_test()
            except Exception as exc:  # noqa: BLE001
                self._result = {"ok": False, "version": APP_VERSION, "install_mode": "unknown",
                                "checks": [{"name": "diagnostics", "ok": False, "detail": str(exc) or type(exc).__name__}]}

        threading.Thread(target=work, daemon=True).start()
        self.app.after(100, self._poll)

    def _poll(self) -> None:
        from diagnostics_window import check_name
        from version import APP_VERSION

        if self.done:
            return
        if self._result is None:
            self.app.after(100, self._poll)
            return
        report, self._result = self._result, None
        self.report = report
        self.checking = False
        checks = list(report.get("checks") or [])
        failures = sum(1 for item in checks if not bool(item.get("ok")))
        mode = str(report.get("install_mode") or "unknown").capitalize()
        if report.get("ok"):
            self.summary = f"All {len(checks)} checks passed. {mode} installation, version {APP_VERSION}."
            self.summary_bad = False
        else:
            self.summary = f"{failures} of {len(checks)} checks failed. {mode} installation, version {APP_VERSION}."
            self.summary_bad = True
        first_fail = next((i for i, item in enumerate(checks) if not item.get("ok")), -1)
        self.selected = first_fail if first_fail >= 0 else (0 if checks else -1)
        del check_name

    def body(self) -> dict[str, Any]:
        from diagnostics_window import check_name

        checks = list((self.report or {}).get("checks") or [])
        rows = [[check_name(item), "OK" if item.get("ok") else "Failed", " ".join(str(item.get("detail") or "").split())]
                for item in checks]
        detail = ""
        if 0 <= self.selected < len(checks):
            item = checks[self.selected]
            detail = f"{check_name(item)}: {item.get('detail') or ''}".strip()
        return vbox(
            pad(label(self.summary, "error" if self.summary_bad else "title"), bottom=GAP),
            table("checks", [("Check", 140), ("Result", 64), ("Details", 440)], rows, height=10, selected=self.selected,
                  rowKinds=["" if item.get("ok") else "error" for item in checks]),
            pad(label(detail, wrap=True, wrap_width=600), top=GAP),
        )

    def buttons(self) -> list[dict[str, Any]]:
        return [push("run", "Checking…" if self.checking else "Run checks", enabled=not self.checking),
                push("copy", "Copy report", enabled=self.report is not None), push("log", "Open log"),
                push("close", "Close", default=True)]

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        if isinstance(values.get("checks"), int):
            self.selected = values["checks"]
        if event == "close" or widget == "close":
            self.shell.diagnostics = None
            self.close()
        elif widget == "run":
            self.run_checks()
        elif widget == "copy" and self.report:
            self.app.clipboard_clear()
            self.app.clipboard_append(json.dumps(self.report, indent=2))
            self.status = "Report copied."
        elif widget == "log":
            self.app.open_app_log()


# --------------------------------------------------------------------------- command palette
class PaletteForm(NativeForm):
    title = "Command palette"
    width = 440

    def __init__(self, shell: Any, commands: list[tuple[str, Callable[[], None]]]) -> None:
        super().__init__(shell)
        self.commands = commands
        self.query = ""
        self.selected = 0
        self.focus = "query"

    def shown(self) -> list[tuple[str, Callable[[], None]]]:
        from command_palette import filter_commands

        return filter_commands(self.commands, self.query)

    def body(self) -> dict[str, Any]:
        rows = [[name] for name, _fn in self.shown()]
        return vbox(
            pad(label("Type a command (Enter runs it, Esc closes):"), bottom=3),
            pad(entry("query", self.query, 50, enter=True, selectAll=False), bottom=GAP),
            table("items", [("Command", 400)], rows, height=12, selected=min(self.selected, len(rows) - 1),
                  headers=False),
        )

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        query = str(values.get("query", self.query))
        if query != self.query:
            self.query = query
            self.selected = 0
        if isinstance(values.get("items"), int) and values["items"] >= 0:
            self.selected = values["items"]
        if event == "close":
            self.close()
        elif (event == "enter" and widget == "query") or (event == "dblclick" and widget == "items") or (
                event == "select" and widget == "items" and False):
            shown = self.shown()
            if shown:
                _name, fn = shown[max(0, min(self.selected, len(shown) - 1))]
                self.close()
                self.app.after(10, fn)


# ------------------------------------------------------------------------- browser download
class CaptureForm(NativeForm):
    """The small "New download" window of browser downloads (capture_dialog.py, natively)."""

    owner = "none"
    width = 540

    def __init__(self, shell: Any, api: Any, *, name: str, folder: str, source: str, size_bytes: int = 0,
                 is_file: bool = True, offset: int = 0, ext: str = "", host: str = "", ext_saved: bool = False,
                 site_saved: bool = False) -> None:
        super().__init__(shell)
        self.api = api
        self.name = name
        self.folder = folder
        self.source = source
        self.size_bytes = size_bytes
        self.is_file = is_file
        self.orig_ext = os.path.splitext(name)[1] if is_file else ""
        self.ext = ext
        self.host = host
        self.remember_ext = bool(ext_saved)
        self.remember_site = bool(site_saved)
        self.offset = offset
        self.topmost = True
        self.mode = "prompt"
        self.request_id = ""
        self.path = ""
        self.kind = ""
        self.state_text = "Starting…"
        self.stats = ""
        self.percent = 0.0
        self.busy = False
        self.window = "New download"
        self.ended_text = ""
        self.focus = "name"
        self._done_size = ""

    def window_title(self) -> str:
        return self.window

    def body(self) -> dict[str, Any]:
        if self.mode == "prompt":
            info = " • ".join(p for p in (dl.format_size(self.size_bytes) or "Size unknown", self.source) if p)
            rows: list[dict[str, Any] | None] = [
                label("Save as:"), span(entry("name", self.name, 62), 2),
                label("Save to:"), entry("folder", self.folder, 50), button("browse", "Browse…", 10),
            ]
            if self.ext:
                rows += [None, span(check("remember_ext", f"Always save {self.ext} files in this folder", self.remember_ext), 2)]
            if self.host:
                rows += [None, span(check("remember_site", f"Always save downloads from {self.host} in this folder",
                                          self.remember_site), 2)]
            return vbox(label("Download this file?", "title"),
                        pad(label(info, "hint", wrap=True, wrap_width=520), top=2, bottom=PAD),
                        grid(3, *rows, weights=[0, 1, 0], colgap=GAP, rowgap=GAP))
        if self.mode == "done":
            return vbox(
                label("Download complete", "title"),
                pad(label(" • ".join(p for p in (Path(self.path).name, self._done_size) if p), wrap=True, wrap_width=520),
                    top=2, bottom=GAP),
                label("Saved in:", "hint"),
                hbox({**entry("path", self.path, 60, readonly=True), "stretch": True}, button("copy_path", "Copy path", 10)),
                pad({"type": "drop", "text": "Drag this file into Telegram, WhatsApp, a browser or a folder",
                     "path": self.path}, top=PAD),
            )
        if self.mode == "deleted":
            return vbox(label("The file was deleted.", "title"))
        return vbox(
            label(self.name, "title", wrap=True, wrap_width=520),
            pad(label(self.state_text), top=2, bottom=GAP),
            progress("bar", self.percent / 100.0, busy=self.busy, length=520),
            pad(label(self.stats, "hint", wrap=True, wrap_width=520), top=GAP) if self.stats else space(0, GAP),
            pad(label(f"Save to: {self.folder}", "hint", wrap=True, wrap_width=520), top=2),
        )

    def buttons(self) -> list[dict[str, Any]]:
        if self.mode == "prompt":
            return [push("download", "Download", default=True, width=14), push("later", "Later", width=14),
                    push("cancel", "Cancel", width=14)]
        if self.mode == "done":
            return [push("open", "Open", default=True, width=14), push("show", "Show in folder", width=14),
                    push("delete", "Delete", width=14), push("close", "Close", width=14)]
        kind = self.kind
        if kind == "running":
            return [push("pause", "Pause", width=14), push("cancel_dl", "Cancel", width=14),
                    push("minimize", "Minimize", width=14)]
        if kind == "paused":
            return [push("resume", "Resume", default=True, width=14), push("cancel_dl", "Cancel", width=14),
                    push("minimize", "Minimize", width=14)]
        if kind == "stopping":
            return [push("minimize", "Minimize", width=14)]
        if kind == "failed":
            return [push("retry", "Retry", default=True, width=14), push("close", "Close", width=14)]
        return [push("close", "Close", default=True, width=14)]

    # -- logic (same as capture_dialog.CaptureDialog) --------------------------------------
    def begin(self, later: bool) -> None:
        name = " ".join(self.name.split())
        folder = self.folder.strip()
        if not name:
            self.status = "Type a file name."
            return
        if not folder:
            self.status = "Choose where to save the file."
            return
        if self.is_file and self.orig_ext and not os.path.splitext(name)[1]:
            name += self.orig_ext
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.status = f"Cannot use that folder: {exc.strerror or exc}"
            return
        try:
            request_id = self.api.start(name, folder, later, {"ext": self.remember_ext, "site": self.remember_site})
        except Exception as exc:  # noqa: BLE001
            self.status = f"Could not start: {exc}"[:90]
            return
        if not request_id:
            self.status = "Could not add the download."
            return
        self.request_id = request_id
        if later:
            self.close()
            return
        self.name = name
        self.folder = folder
        self.mode = "progress"
        self.window = "Downloading"
        self.kind = "running"
        self.status = ""
        self.topmost = False
        self.tick()

    def tick(self) -> None:
        if self.done or self.mode != "progress":
            return
        try:
            self.refresh()
        except Exception:  # noqa: BLE001
            pass
        if self.mode == "progress" and not self.done:
            self.app.after(400, self.tick)

    def refresh(self) -> None:
        snap, metrics = self.api.state(self.request_id)
        if snap is None:
            self.state_text = "This download was removed from the list."
            self.busy = False
            self.kind = "removed"
            self.mode = "ended"
            return
        status = str(snap.get("status") or "")
        name = str(snap.get("name") or "")
        if name:
            self.name = name
        percent = max(0.0, min(100.0, float(snap.get("progress") or 0.0) * 100.0))
        if status == "completed":
            self.path = str(snap.get("result_path") or "")
            self.percent = 100.0
            try:
                self._done_size = dl.format_size(os.path.getsize(self.path))
            except OSError:
                self._done_size = ""
            self.mode = "done"
            self.window = "Download complete"
            self.topmost = True
            self.flash = True
            return
        if status == "failed":
            self.state_text = "The download failed."
            self.stats = str(snap.get("error") or snap.get("detail") or "")[:300]
            self.window = "Download failed"
            self.kind = "failed"
            self.busy = False
            return
        if status == "cancelled":
            self.state_text = "Download cancelled."
            self.window = "Download cancelled"
            self.kind = "cancelled"
            self.mode = "ended"
            self.busy = False
            return
        if status == "paused":
            self.state_text = "Paused"
            self.kind = "paused"
            self.percent = percent
            self.busy = False
            return
        if status == "cancelling":
            self.state_text = str(snap.get("detail") or "Stopping…")
            self.kind = "stopping"
            return
        self.kind = "running"
        detail = str(snap.get("detail") or "")
        self.state_text = (f"{percent:.0f}%  •  {detail}" if percent >= 1 and detail else
                           (f"{percent:.0f}%" if percent >= 1 else (detail or "Starting…")))
        self.percent = percent
        self.busy = percent < 1
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
        self.stats = "  •  ".join(parts)
        self.window = f"{percent:.0f}% - {name or 'Downloading'}"

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        from tkinter import filedialog, messagebox

        if self.mode == "prompt":
            self.name = str(values.get("name", self.name))
            self.folder = str(values.get("folder", self.folder))
            self.remember_ext = bool(values.get("remember_ext", self.remember_ext))
            self.remember_site = bool(values.get("remember_site", self.remember_site))
        if event == "close":
            # Closing the window never stops a download that is running: it carries on in the list.
            self.close()
            return
        if event != "click" and event != "enter":
            return
        if widget == "download":
            self.begin(False)
        elif widget == "later":
            self.begin(True)
        elif widget in {"cancel", "close"}:
            self.close()
        elif widget == "browse":
            start = self.folder
            selected = filedialog.askdirectory(title="Save to", initialdir=start if os.path.isdir(start) else None)
            if selected:
                self.folder = os.path.normpath(selected)
                self.offset = self.offset  # keep position
        elif widget == "pause":
            self.api.pause(self.request_id)
        elif widget == "resume":
            self.api.resume(self.request_id)
        elif widget == "cancel_dl":
            self.api.cancel(self.request_id)
            self.close()
        elif widget == "retry":
            self.api.retry(self.request_id)
        elif widget == "minimize":
            self.shell.minimize_form(self)
        elif widget == "copy_path":
            self.app.clipboard_clear()
            self.app.clipboard_append(self.path)
            self.status = "Path copied."
        elif widget == "open":
            if self.path and os.path.exists(self.path):
                try:
                    os.startfile(self.path)  # type: ignore[attr-defined]
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror("Media Downloader", f"Could not open it.\n\n{exc}")
            else:
                messagebox.showwarning("Media Downloader", "The file is no longer on the disk.")
        elif widget == "show":
            reveal(self.path or self.folder)
        elif widget == "delete":
            if not self.path or not os.path.exists(self.path):
                messagebox.showwarning("Media Downloader", "The file is no longer on the disk.")
                return
            if not messagebox.askyesno("Delete file",
                                       f"Delete this file from the disk?\n\n{self.path}\n\nThis cannot be undone.",
                                       icon="warning"):
                return
            try:
                os.remove(self.path)
            except OSError as exc:
                messagebox.showerror("Media Downloader", f"Could not delete the file.\n\n{exc}")
                return
            self.api.deleted(self.request_id, self.path)
            self.mode = "deleted"
            self.kind = "deleted"


def reveal(path: str) -> None:
    target = Path(os.path.normpath(path))
    try:
        if os.name == "nt":
            if target.is_file():
                subprocess.Popen(f'explorer /select,"{target}"')
            else:
                os.startfile(str(target if target.is_dir() else target.parent))  # type: ignore[attr-defined]
    except Exception:
        pass
