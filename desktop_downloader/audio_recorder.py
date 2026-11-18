"""Audio recorder window: record a call or a meeting (their voice + yours) to MP3 and/or WAV.

The other side of a WhatsApp / Messenger / Telegram / Zoom / Google Meet call comes out of the
speaker or headset, so it is captured from that output device; your own voice is captured from
the microphone. Both are mixed into one file. Only sound is recorded, no video.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import audio_recorder_engine as audio
import native
import screen_recorder_engine as engine
from app_logging import get_logger
from screen_recorder import FlatButton, LevelMeter, _open_folder
from theme import C
from ui_kit import px

LOGGER = get_logger("audio_recorder")

APP_TITLE = "Audio Recorder"
DEFAULTS: dict[str, Any] = {
    "system_audio": True,
    "speaker": "",
    "system_volume": 100,
    "mic_enabled": True,
    "microphone": "",
    "mic_volume": 100,
    "save_wav": True,
    "save_mp3": True,
    "mp3_bitrate": "192 kbps",
    "output_dir": "",
}
RED = "#E5322D"
GREEN = "#1FA35B"
GREY = "#5B6470"


def format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class AudioRecorderWindow(tk.Toplevel):
    def __init__(self, parent: Any, *, output_dir: Path, settings: dict[str, Any] | None = None,
                 save_settings: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__(parent)
        self.default_output_dir = Path(output_dir)
        self.save_callback = save_settings
        self.config_data = {**DEFAULTS, **(settings or {})}
        self.ui_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()
        self.session: audio.AudioSession | None = None
        self.state = "idle"        # idle, starting, recording, paused, finishing
        self.microphones: list[str] = []
        self.speakers: list[str] = []
        self.monitors_live: dict[str, engine.LevelMonitor] = {}
        self._closing = False
        self._tick_id: str | None = None

        self.title(APP_TITLE)
        self.configure(bg=native.dialog_bg(self))
        self.resizable(False, False)
        try:
            icon = getattr(parent, "_icon_file", None)
            if icon:
                self.iconbitmap(icon)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)

        c = self.config_data
        self.system_var = tk.BooleanVar(self, value=bool(c["system_audio"]))
        self.speaker_var = tk.StringVar(self, value=str(c["speaker"] or ""))
        self.system_volume_var = tk.IntVar(self, value=int(c["system_volume"]))
        self.mic_var = tk.BooleanVar(self, value=bool(c["mic_enabled"]))
        self.mic_device_var = tk.StringVar(self, value=str(c["microphone"] or ""))
        self.mic_volume_var = tk.IntVar(self, value=int(c["mic_volume"]))
        self.wav_var = tk.BooleanVar(self, value=bool(c["save_wav"]))
        self.mp3_var = tk.BooleanVar(self, value=bool(c["save_mp3"]))
        self.bitrate_var = tk.StringVar(self, value=c["mp3_bitrate"] if c["mp3_bitrate"] in audio.MP3_BITRATES
                                        else "192 kbps")
        self.output_dir = Path(c["output_dir"]) if c.get("output_dir") else self.default_output_dir
        self.clock_var = tk.StringVar(self, value="00:00:00")
        self.status_var = tk.StringVar(self, value="Ready")

        self._build()
        self._sync_buttons()
        self.after(40, self._drain_queue)
        threading.Thread(target=self._probe_devices, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        body = ttk.Frame(self, padding=native.PAD)
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        meter_bg = native.dialog_bg(self)

        sound = native.group(body, "Sound to record", row=0, column=0, sticky="ew")
        sound.grid_columnconfigure(1, weight=1)
        self.system_check = ttk.Checkbutton(sound, text="Other side of the call / meeting (what you hear)",
                                            variable=self.system_var, command=self._restart_monitors)
        self.system_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        self.speaker_menu = native.combo(sound, self.speaker_var, [], width=44)
        self.speaker_menu.grid(row=1, column=0, columnspan=2, sticky="ew", pady=2)
        self.speaker_menu.bind("<<ComboboxSelected>>", lambda _e: self._restart_monitors(), add="+")
        native.Text(sound, "Volume:").grid(row=2, column=0, sticky="w")
        ttk.Scale(sound, from_=0, to=200, variable=self.system_volume_var, orient="horizontal").grid(
            row=2, column=1, sticky="ew", padx=(native.GAP, 0))
        native.Text(sound, "Level:").grid(row=3, column=0, sticky="w")
        self.system_meter = LevelMeter(sound, width=220, height=8, bg=meter_bg)
        self.system_meter.grid(row=3, column=1, sticky="ew", padx=(native.GAP, 0), pady=2)

        self.mic_check = ttk.Checkbutton(sound, text="Your voice (microphone)", variable=self.mic_var,
                                         command=self._restart_monitors)
        self.mic_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=(native.GAP, 2))
        self.mic_menu = native.combo(sound, self.mic_device_var, [], width=44)
        self.mic_menu.grid(row=5, column=0, columnspan=2, sticky="ew", pady=2)
        self.mic_menu.bind("<<ComboboxSelected>>", lambda _e: self._restart_monitors(), add="+")
        native.Text(sound, "Volume:").grid(row=6, column=0, sticky="w")
        ttk.Scale(sound, from_=0, to=200, variable=self.mic_volume_var, orient="horizontal").grid(
            row=6, column=1, sticky="ew", padx=(native.GAP, 0))
        native.Text(sound, "Level:").grid(row=7, column=0, sticky="w")
        self.mic_meter = LevelMeter(sound, width=220, height=8, bg=meter_bg)
        self.mic_meter.grid(row=7, column=1, sticky="ew", padx=(native.GAP, 0), pady=2)
        self.note = native.Text(
            sound, "Pick the device your call plays on (usually your headphones). Keep the call volume on: "
                   "a muted call is recorded as silence. Headphones avoid an echo of the other person.",
            wrap=px(360))
        self.note.configure(foreground=C["MUTED"])
        self.note.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(native.GAP, 0))

        save = native.group(body, "Save as", row=1, column=0, sticky="ew", pady=(native.PAD, 0))
        ttk.Checkbutton(save, text="WAV (best quality, big file)", variable=self.wav_var,
                        command=self._ensure_one_format).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(save, text="MP3", variable=self.mp3_var, command=self._ensure_one_format).grid(
            row=1, column=0, sticky="w", pady=2)
        native.combo(save, self.bitrate_var, list(audio.MP3_BITRATES), width=10).grid(
            row=1, column=1, sticky="w", padx=(native.GAP, 0))
        native.Text(save, "Folder:").grid(row=2, column=0, sticky="w", pady=(native.GAP, 0))
        self.folder_label = native.Text(save, str(self.output_dir), wrap=px(300))
        self.folder_label.grid(row=2, column=1, sticky="w", padx=(native.GAP, 0), pady=(native.GAP, 0))
        row = ttk.Frame(save)
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Button(row, text="Change…", command=self.choose_folder).pack(side="left")
        ttk.Button(row, text="Open folder", command=self.open_folder).pack(side="left", padx=(native.GAP, 0))

        control = ttk.Frame(body)
        control.grid(row=2, column=0, sticky="ew", pady=(native.PAD, 0))
        control.grid_columnconfigure(0, weight=1)
        self.clock_label = tk.Label(control, textvariable=self.clock_var, font=("Segoe UI", 22, "bold"),
                                    bg=native.dialog_bg(self), fg=C["TEXT"])
        self.clock_label.grid(row=0, column=0, columnspan=3, pady=(0, 2))
        self.status_label = ttk.Label(control, textvariable=self.status_var, style="Hint.TLabel")
        self.status_label.grid(row=1, column=0, columnspan=3, pady=(0, native.GAP))
        buttons = ttk.Frame(control)
        buttons.grid(row=2, column=0, columnspan=3)
        self.start_button = FlatButton(buttons, "●  Start recording", self.start_recording, bg=RED, padx=16, pady=7)
        self.start_button.pack(side="left", padx=3)
        self.pause_button = FlatButton(buttons, "❚❚  Pause", self.toggle_pause, bg=GREY, padx=16, pady=7)
        self.pause_button.pack(side="left", padx=3)
        self.stop_button = FlatButton(buttons, "■  Stop and save", self.stop_recording, bg=GREEN, padx=16, pady=7)
        self.stop_button.pack(side="left", padx=3)

    # ------------------------------------------------------------------ settings
    def _collect_settings(self) -> dict[str, Any]:
        return {
            "system_audio": bool(self.system_var.get()),
            "speaker": self.speaker_var.get(),
            "system_volume": int(self.system_volume_var.get()),
            "mic_enabled": bool(self.mic_var.get()),
            "microphone": self.mic_device_var.get(),
            "mic_volume": int(self.mic_volume_var.get()),
            "save_wav": bool(self.wav_var.get()),
            "save_mp3": bool(self.mp3_var.get()),
            "mp3_bitrate": self.bitrate_var.get(),
            "output_dir": str(self.output_dir) if self.output_dir != self.default_output_dir else "",
        }

    def _save(self) -> None:
        self.config_data = self._collect_settings()
        if self.save_callback is not None:
            try:
                self.save_callback(dict(self.config_data))
            except Exception:
                LOGGER.exception("Could not save audio recorder settings")

    def _ensure_one_format(self) -> None:
        if not self.wav_var.get() and not self.mp3_var.get():
            self.mp3_var.set(True)

    # ------------------------------------------------------------------ devices and meters
    def _probe_devices(self) -> None:
        error = engine.audio_backend_error()
        speakers = [] if error else engine.list_speakers()
        mics = [] if error else engine.list_microphones()
        self.ui_queue.put((self._devices_ready, (speakers, mics, error)))

    def _devices_ready(self, speakers: list[str], mics: list[str], error: str) -> None:
        self.speakers, self.microphones = speakers, mics
        self.speaker_menu.configure(values=speakers)
        self.mic_menu.configure(values=mics)
        if speakers and self.speaker_var.get() not in speakers:
            self.speaker_var.set(speakers[0])
        if mics and self.mic_device_var.get() not in mics:
            self.mic_device_var.set(mics[0])
        if error:
            self.status_var.set(error)
        elif not speakers and not mics:
            self.status_var.set("No sound devices were found.")
        self._restart_monitors()

    def _restart_monitors(self) -> None:
        """Live meters while idle, so the user sees that both sides are heard before starting."""
        self._stop_monitors()
        if self._closing or self.state != "idle" or engine.audio_backend_error():
            return
        if self.system_var.get() and self.speakers:
            monitor = engine.LevelMonitor(loopback=True, device=self.speaker_var.get() or None)
            monitor.start()
            self.monitors_live["system"] = monitor
        if self.mic_var.get() and self.microphones:
            monitor = engine.LevelMonitor(loopback=False, device=self.mic_device_var.get() or None)
            monitor.start()
            self.monitors_live["mic"] = monitor
        self._ensure_tick()

    def _stop_monitors(self) -> None:
        for monitor in self.monitors_live.values():
            monitor.stop()
        self.monitors_live = {}

    def _ensure_tick(self) -> None:
        if self._tick_id is None and not self._closing:
            self._tick_id = self.after(60, self._tick)

    def _tick(self) -> None:
        self._tick_id = None
        if self._closing:
            return
        system = mic = 0.0
        if self.session is not None and self.state in {"recording", "paused"}:
            system, mic = self.session.levels()
            self.clock_var.set(format_clock(self.session.elapsed()))
            if self.state == "recording":
                self.status_var.set("Recording…")
        else:
            monitor = self.monitors_live.get("system")
            system = monitor.level if monitor else 0.0
            monitor = self.monitors_live.get("mic")
            mic = monitor.level if monitor else 0.0
        self.system_meter.set(system)
        self.mic_meter.set(mic)
        if self.monitors_live or self.state in {"recording", "paused"}:
            self._ensure_tick()

    # ------------------------------------------------------------------ actions
    def _sync_buttons(self) -> None:
        idle = self.state == "idle"
        busy = self.state in {"recording", "paused"}
        self.start_button.set_enabled(idle)
        self.pause_button.set_enabled(busy)
        self.stop_button.set_enabled(busy)
        self.pause_button.configure(text="▶  Resume" if self.state == "paused" else "❚❚  Pause")

    def _settings(self) -> audio.AudioSettings:
        mic = self.mic_device_var.get() if self.mic_var.get() and self.microphones else None
        return audio.AudioSettings(
            output_dir=self.output_dir,
            system_audio=bool(self.system_var.get()) and bool(self.speakers),
            speaker=self.speaker_var.get() or None,
            microphone=mic or None,
            system_volume=max(0.0, int(self.system_volume_var.get()) / 100.0),
            mic_volume=max(0.0, int(self.mic_volume_var.get()) / 100.0),
            save_wav=bool(self.wav_var.get()),
            save_mp3=bool(self.mp3_var.get()),
            mp3_bitrate=audio.MP3_BITRATES.get(self.bitrate_var.get(), 192),
        )

    def start_recording(self) -> None:
        if self.state != "idle":
            return
        try:
            session = audio.AudioSession(self._settings(), log=LOGGER.info)
        except audio.AudioRecordingError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self)
            return
        self._save()
        self._stop_monitors()
        self.session = session
        self.state = "starting"
        self.status_var.set("Starting…")
        self.start_button.set_enabled(False)

        def work() -> None:
            try:
                session.start()
                self.ui_queue.put((self._started, ("",)))
            except Exception as exc:
                LOGGER.exception("Audio recording did not start")
                self.ui_queue.put((self._started, (str(exc) or "Could not start recording.",)))

        threading.Thread(target=work, daemon=True).start()

    def _started(self, error: str) -> None:
        if error:
            self.state = "idle"
            self.session = None
            self.status_var.set("Ready")
            self._sync_buttons()
            messagebox.showerror(APP_TITLE, error, parent=self)
            self._restart_monitors()
            return
        self.state = "recording"
        self.clock_var.set("00:00:00")
        self._sync_buttons()
        warnings = self.session.warnings if self.session else []
        self.status_var.set(("Recording… " + "; ".join(warnings)) if warnings else "Recording…")
        self._ensure_tick()

    def toggle_pause(self) -> None:
        session = self.session
        if session is None:
            return
        if self.state == "recording":
            session.pause()
            self.state = "paused"
            self.status_var.set("Paused")
            self._sync_buttons()
        elif self.state == "paused":
            self.state = "starting"
            self.pause_button.set_enabled(False)
            self.stop_button.set_enabled(False)

            def work() -> None:
                try:
                    session.resume()
                    self.ui_queue.put((self._resumed, ("",)))
                except Exception as exc:
                    self.ui_queue.put((self._resumed, (str(exc) or "Could not resume.",)))

            threading.Thread(target=work, daemon=True).start()

    def _resumed(self, error: str) -> None:
        if error:
            messagebox.showerror(APP_TITLE, error, parent=self)
            self.state = "paused"
        else:
            self.state = "recording"
            self.status_var.set("Recording…")
        self._sync_buttons()
        self._ensure_tick()

    def stop_recording(self) -> None:
        session = self.session
        if session is None or self.state not in {"recording", "paused"}:
            return
        self.state = "finishing"
        self.status_var.set("Saving…")
        self._sync_buttons()

        def work() -> None:
            try:
                files = session.stop()
                self.ui_queue.put((self._finished, (files, "")))
            except Exception as exc:
                LOGGER.exception("Audio recording could not be saved")
                self.ui_queue.put((self._finished, ([], str(exc) or "Could not save the recording.")))

        threading.Thread(target=work, daemon=True).start()

    def _finished(self, files: list[Path], error: str) -> None:
        self.session = None
        self.state = "idle"
        self._sync_buttons()
        self.clock_var.set("00:00:00")
        if error:
            self.status_var.set("Ready")
            messagebox.showerror(APP_TITLE, error, parent=self)
        else:
            names = ", ".join(path.name for path in files)
            self.status_var.set(f"Saved: {names}")
            LOGGER.info("Audio recording saved: %s", names)
            if files:
                try:
                    _open_folder(files[0])
                except Exception:
                    pass
        self._restart_monitors()

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, initialdir=str(self.output_dir), title="Save audio to")
        if folder:
            self.output_dir = Path(folder)
            self.folder_label.configure(text=str(self.output_dir))
            self._save()

    def open_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(str(self.output_dir))  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------------------------------------------ window
    def show(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _drain_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                func, args = self.ui_queue.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.after(40, self._drain_queue)

    def close(self) -> None:
        if self.state in {"recording", "paused", "starting", "finishing"}:
            if self.state == "finishing":
                messagebox.showinfo(APP_TITLE, "Saving the recording… please wait a moment.", parent=self)
                return
            if not messagebox.askyesno(APP_TITLE, "A recording is in progress. Stop and discard it?", parent=self):
                return
            if self.session is not None:
                self.session.abort()
        self._closing = True
        self._stop_monitors()
        self._save()
        try:
            self.destroy()
        except tk.TclError:
            pass
