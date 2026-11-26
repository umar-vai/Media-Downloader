"""Native (C++) windows of the audio recorder and the screen recorder.

The recorder logic stays in ``audio_recorder.AudioRecorderWindow`` and
``screen_recorder.ScreenRecorderWindow``: each is created as before, then made invisible
(transparent and off-screen), and this module mirrors its controls into a native form. Edits in
the native window are written back into the Tk variables and run the same callbacks, so the two
can never disagree. When the recorder hides itself (area selection, recording) the native window
hides too; its overlays (area selector, control bar, countdown) are its own small windows.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

import native_forms as nf
from native_forms import GAP, PAD, button, check, combo, grid, group, hbox, label, pad, push, radio, span, vbox

RED = "#E5322D"
GREEN = "#1FA35B"
GREY = "#5B6470"


def ghost(window: tk.Toplevel) -> None:
    """Keeps a Tk window working but invisible (the native form is shown instead)."""
    def hide(_event: Any = None) -> None:
        try:
            window.attributes("-alpha", 0.0)
            window.geometry("+-32000+-32000")
        except tk.TclError:
            pass
    try:
        window.attributes("-toolwindow", True)
    except tk.TclError:
        pass
    hide()
    window.bind("<Map>", hide, add="+")


def text_of(widget: Any) -> str:
    try:
        return str(widget.cget("text"))
    except (tk.TclError, AttributeError):
        return ""


def values_of(widget: Any) -> list[str]:
    try:
        return [str(v) for v in widget.cget("values")]
    except (tk.TclError, AttributeError):
        return []


def is_off(widget: Any) -> bool:
    try:
        if hasattr(widget, "instate"):
            return bool(widget.instate(["disabled"]))
        return str(widget.cget("state")) == "disabled"
    except tk.TclError:
        return False


def slider(wid: str, value: int, *, length: int = 160, low: int = 0, high: int = 200) -> dict[str, Any]:
    return {"type": "slider", "id": wid, "value": int(value), "min": low, "max": high, "length": length}


def note(widget: Any, width: int = 240, top: int = 0) -> dict[str, Any]:
    """A hint line that is hidden while empty (it keeps its place in the window's structure)."""
    text = text_of(widget)
    return pad(label(text, "hint", wrap=True, wrap_width=width, hidden=not text), top=top)


def meter(wid: str, widget: Any, length: int = 160) -> dict[str, Any]:
    level = float(getattr(widget, "value", 0.0) or 0.0)
    color = "#E5322D" if level > 0.92 else "#F5B301" if level > 0.75 else "#1FA35B"
    return {**nf.progress(wid, level, length=length, height=8), "color": color}


class TkMirrorForm(nf.NativeForm):
    """Base: the native window shows/hides with the Tk window it mirrors."""

    resizable = False

    def __init__(self, shell: Any, window: tk.Toplevel) -> None:
        super().__init__(shell)
        self.window = window
        ghost(window)

    def alive(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def spec(self) -> dict[str, Any]:
        if not self.alive():
            self.app.after(0, self.close)
            return {"id": self.form_id, "title": self.title, "body": vbox(), "buttons": [], "hidden": True}
        spec = super().spec()
        try:
            spec["hidden"] = self.window.wm_state() not in {"normal", "zoomed"}
        except tk.TclError:
            spec["hidden"] = True
        return spec

    def write(self, variable: Any, value: Any, callback: Callable[[], Any] | None = None) -> bool:
        try:
            if variable.get() == value:
                return False
            variable.set(value)
        except (tk.TclError, ValueError):
            return False
        if callback is not None:
            callback()
        return True

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        if event == "close":
            self.window.close()
            if not self.alive():
                self.close()


# ----------------------------------------------------------------------------- audio recorder
class AudioRecorderForm(TkMirrorForm):
    title = "Audio Recorder"
    owner = "none"

    def body(self) -> dict[str, Any]:
        w = self.window
        speakers = values_of(w.speaker_menu)
        mics = values_of(w.mic_menu)
        sound = vbox(
            check("system", text_of(w.system_check), w.system_var.get()),
            pad(combo("speaker", speakers, w.speaker_var.get(), 44, align="fill"), top=2),
            grid(2,
                 label("Volume:"), slider("system_volume", w.system_volume_var.get(), length=220),
                 label("Level:"), meter("system_meter", w.system_meter, 220),
                 weights=[0, 1], colgap=GAP, rowgap=2),
            pad(check("mic", text_of(w.mic_check), w.mic_var.get()), top=GAP),
            pad(combo("mic_device", mics, w.mic_device_var.get(), 44, align="fill"), top=2),
            grid(2,
                 label("Volume:"), slider("mic_volume", w.mic_volume_var.get(), length=220),
                 label("Level:"), meter("mic_meter", w.mic_meter, 220),
                 weights=[0, 1], colgap=GAP, rowgap=2),
            pad(label(text_of(w.note), "hint", wrap=True, wrap_width=360), top=GAP),
        )
        import audio_recorder_engine as audio

        save = grid(
            3,
            span(check("wav", "WAV (best quality, big file)", w.wav_var.get()), 3),
            check("mp3", "MP3", w.mp3_var.get()), span(combo("bitrate", list(audio.MP3_BITRATES), w.bitrate_var.get(), 10), 2),
            label("Folder:"), span(label(text_of(w.folder_label), wrap=True, wrap_width=300), 2),
            span(hbox(button("change", "Change…", 10), button("open_folder", "Open folder", 11)), 3),
            weights=[0, 0, 1], colgap=GAP, rowgap=2,
        )
        busy = w.state in {"recording", "paused"}
        control = vbox(
            label(w.clock_var.get(), "clock"),
            pad(label(w.status_var.get(), "hint", align="center"), top=2, bottom=GAP),
            hbox(
                {**button("start", "●  Start recording", 16, enabled=w.state == "idle"), "color": RED},
                {**button("pause", "▶  Resume" if w.state == "paused" else "❚❚  Pause", 11, enabled=busy), "color": GREY},
                {**button("stop", "■  Stop and save", 15, enabled=busy), "color": GREEN},
                align="center",
            ),
        )
        return vbox(group("Sound to record", sound), pad(group("Save as", save), top=PAD), pad(control, top=PAD))

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        w = self.window
        if event in {"change", "click"}:
            restart = False
            restart |= self.write(w.system_var, bool(values.get("system", w.system_var.get())))
            restart |= self.write(w.mic_var, bool(values.get("mic", w.mic_var.get())))
            if values.get("speaker"):
                restart |= self.write(w.speaker_var, values["speaker"])
            if values.get("mic_device"):
                restart |= self.write(w.mic_device_var, values["mic_device"])
            for key, var in (("system_volume", w.system_volume_var), ("mic_volume", w.mic_volume_var)):
                if isinstance(values.get(key), int):
                    self.write(var, values[key])
            self.write(w.wav_var, bool(values.get("wav", w.wav_var.get())))
            self.write(w.mp3_var, bool(values.get("mp3", w.mp3_var.get())))
            w._ensure_one_format()
            if values.get("bitrate"):
                self.write(w.bitrate_var, values["bitrate"])
            if restart:
                w._restart_monitors()
        if event == "click":
            action = {"start": w.start_recording, "pause": w.toggle_pause, "stop": w.stop_recording,
                      "change": w.choose_folder, "open_folder": w.open_folder}.get(widget)
            if action is not None:
                action()
        super().handle(event, widget, values)


# ----------------------------------------------------------------------------- screen recorder
class ScreenRecorderForm(TkMirrorForm):
    title = "Screen Recorder"
    owner = "none"

    def body(self) -> dict[str, Any]:
        import screen_recorder as sr
        import screen_recorder_engine as engine

        w = self.window
        mode = w.mode_var.get()
        area = grid(
            3,
            radio("fullscreen", "mode", "Full screen", mode == "fullscreen"),
            span(combo("monitor", values_of(w.monitor_menu), w.monitor_var.get(), 28, enabled=not is_off(w.monitor_menu)), 2),
            radio("region", "mode", "Select area", mode == "region"),
            span(hbox(button("select", text_of(w.select_button) or "Select area…", 12, enabled=not is_off(w.select_button)),
                      pad(label("Ratio:"), x=0),
                      combo("ratio", list(sr.RATIOS), w.ratio_var.get(), 6),
                      label(text_of(w.region_label)), gap=GAP), 2),
            radio("fixed", "mode", "Fixed size", mode == "fixed"),
            combo("fixed_size", list(sr.FIXED_SIZES), w.fixed_var.get(), 20, enabled=not is_off(w.fixed_menu)),
            button("place", text_of(w.place_button) or "Place…", 9, enabled=not is_off(w.place_button), align="left"),
            weights=[0, 0, 1], colgap=GAP, rowgap=2,
        )
        video = grid(
            2,
            label("Quality:"), combo("quality", list(engine.QUALITIES), w.quality_var.get(), 12),
            label("Frame rate:"), combo("fps", [str(v) for v in engine.FRAME_RATES], w.fps_var.get(), 12),
            label("Size:"), combo("resolution", list(engine.RESOLUTIONS), w.resolution_var.get(), 12),
            label("Format:"), combo("container", list(engine.CONTAINERS), w.container_var.get(), 12),
            label("Encoder:"), combo("encoder", values_of(w.encoder_menu), w.encoder_var.get(), 18),
            label("Capture:"), combo("capture", list(sr.CAPTURE_ENGINES), w.engine_var.get(), 18),
            span(label(text_of(w.encoder_note), "hint", wrap=True, wrap_width=230), 2),
            weights=[0, 1], colgap=GAP, rowgap=2,
        )
        sound = vbox(
            check("system", text_of(w.system_check), w.system_var.get()),
            grid(2, label("Volume:"), slider("system_volume", w.system_volume_var.get()),
                 label("Level:"), meter("system_meter", w.system_meter), weights=[0, 1], colgap=GAP, rowgap=2),
            pad(check("mic", text_of(w.mic_check), w.mic_var.get()), top=GAP),
            pad(combo("mic_device", values_of(w.mic_menu), w.mic_device_var.get(), 28, enabled=not is_off(w.mic_menu),
                      align="fill"), top=2),
            grid(2, label("Volume:"), slider("mic_volume", w.mic_volume_var.get()),
                 label("Level:"), meter("mic_meter", w.mic_meter), weights=[0, 1], colgap=GAP, rowgap=2),
            note(w.meter_note),
            note(w.audio_note, top=2),
        )
        extras = vbox(
            check("webcam", text_of(w.webcam_check), w.webcam_var.get()),
            pad(combo("webcam_device", values_of(w.webcam_menu), w.webcam_device_var.get(), 26,
                      enabled=not is_off(w.webcam_menu)), top=2),
            pad(hbox(combo("webcam_position", list(engine.WEBCAM_POSITIONS), w.webcam_position_var.get(), 12,
                           enabled=not is_off(w.webcam_position_menu)),
                     combo("webcam_size", list(engine.WEBCAM_SIZES), w.webcam_size_var.get(), 8,
                           enabled=not is_off(w.webcam_size_menu))), top=2),
            pad(check("mouse", "Show mouse cursor", w.mouse_var.get()), top=GAP),
            check("clicks", text_of(w.clicks_check), w.clicks_var.get(), enabled=not is_off(w.clicks_check)),
        )
        options = vbox(
            hbox(label("Countdown:"), combo("countdown", list(sr.COUNTDOWNS), w.countdown_var.get(), 6)),
            pad(hbox(check("auto_stop", "Stop after", w.auto_stop_var.get()),
                     {"type": "spin", "id": "auto_stop_minutes", "value": str(w.auto_stop_minutes_var.get()), "min": 1,
                      "max": 600, "width": 5},
                     label("minutes")), top=2),
            pad(check("open_editor", "Open in the media editor when done", w.open_editor_var.get()), top=2),
            pad(hbox(label("Hotkey:"), combo("hotkey", list(sr.HOTKEYS), w.hotkey_var.get(), 6)), top=2),
            note(w.hotkey_note),
            pad(grid(2, label("Save to:", valign="top"), label(text_of(w.folder_label), wrap=True, wrap_width=200),
                     weights=[0, 1], colgap=GAP), top=GAP),
            pad(hbox(button("browse", "Browse…", 10), button("open_folder", "Open folder", 11)), top=2),
        )
        bottom = hbox(
            {**label(text_of(w.status_label), wrap=True, wrap_width=420), "stretch": True},
            button("pause", text_of(w.pause_button) or "Pause", 9, enabled=not is_off(w.pause_button)),
            button("record", text_of(w.record_button) or "● Record", 12, enabled=not is_off(w.record_button), default=True),
        )
        return vbox(
            group("Capture area", area),
            pad(grid(2, group("Video", video, valign="top"), group("Sound", sound, valign="top"),
                     group("Webcam and mouse", extras, valign="top"), group("Recording", options, valign="top"),
                     weights=[1, 1], colgap=GAP, rowgap=PAD), top=PAD),
            pad(bottom, top=PAD),
        )

    def handle(self, event: str, widget: str, values: dict[str, Any]) -> None:
        w = self.window
        if event in {"change", "click"}:
            for mode in ("fullscreen", "region", "fixed"):
                if values.get(mode) and w.mode_var.get() != mode:
                    w.mode_var.set(mode)
                    (w._region_mode_picked if mode == "region" else w._sync_mode)()
            if values.get("monitor"):
                self.write(w.monitor_var, values["monitor"], w._update_region_label)
            if values.get("ratio"):
                self.write(w.ratio_var, values["ratio"], w._ratio_changed)
            if values.get("fixed_size"):
                self.write(w.fixed_var, values["fixed_size"], w._fixed_changed)
            for key, var in (("quality", w.quality_var), ("fps", w.fps_var), ("resolution", w.resolution_var),
                             ("container", w.container_var), ("encoder", w.encoder_var), ("capture", w.engine_var),
                             ("webcam_position", w.webcam_position_var), ("webcam_size", w.webcam_size_var),
                             ("countdown", w.countdown_var)):
                if values.get(key):
                    self.write(var, values[key])
            if values.get("hotkey"):
                self.write(w.hotkey_var, values["hotkey"], w._register_hotkeys)
            self.write(w.system_var, bool(values.get("system", w.system_var.get())), w._restart_monitors)
            self.write(w.mic_var, bool(values.get("mic", w.mic_var.get())), w._sync_audio)
            if values.get("mic_device"):
                self.write(w.mic_device_var, values["mic_device"], w._restart_monitors)
            for key, var in (("system_volume", w.system_volume_var), ("mic_volume", w.mic_volume_var)):
                if isinstance(values.get(key), int):
                    self.write(var, values[key])
            self.write(w.webcam_var, bool(values.get("webcam", w.webcam_var.get())), w._sync_webcam)
            if values.get("webcam_device"):
                self.write(w.webcam_device_var, values["webcam_device"])
            self.write(w.mouse_var, bool(values.get("mouse", w.mouse_var.get())))
            self.write(w.clicks_var, bool(values.get("clicks", w.clicks_var.get())))
            self.write(w.auto_stop_var, bool(values.get("auto_stop", w.auto_stop_var.get())))
            if values.get("auto_stop_minutes"):
                self.write(w.auto_stop_minutes_var, str(values["auto_stop_minutes"]))
            self.write(w.open_editor_var, bool(values.get("open_editor", w.open_editor_var.get())))
        if event == "click":
            action = {"select": w.select_region, "place": w.select_region, "pause": w.toggle_pause,
                      "record": w.record_clicked, "browse": w.choose_folder,
                      "open_folder": w.open_output_folder}.get(widget)
            if action is not None:
                action()
        super().handle(event, widget, values)


def attach(shell: Any, window: tk.Toplevel, kind: str) -> nf.NativeForm:
    """Shows a recorder window natively (once per window)."""
    existing = getattr(window, "_native_form", None)
    if existing is not None and not existing.done:
        existing.flash = not existing.flash
        return existing
    form = AudioRecorderForm(shell, window) if kind == "audio" else ScreenRecorderForm(shell, window)
    window._native_form = form  # type: ignore[attr-defined]
    shell.show_form(form)
    return form
