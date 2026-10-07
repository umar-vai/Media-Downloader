from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app_logging import get_logger


LOGGER = get_logger("player")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PlayerUnavailableError(RuntimeError):
    pass


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _mpv_executable() -> Path:
    override = os.environ.get("MEDIA_DOWNLOADER_MPV")
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate

    bundled = _resource_path("mpv.exe")
    if bundled.exists():
        return bundled

    local = Path(__file__).resolve().with_name("mpv.exe")
    if local.exists():
        return local

    raise PlayerUnavailableError("Bundled mpv playback engine is missing.")


def _peek_pipe_bytes(stream: Any) -> int:
    if os.name != "nt":
        return 0
    available = ctypes.c_ulong(0)
    handle = msvcrt.get_osfhandle(stream.fileno())
    ok = ctypes.windll.kernel32.PeekNamedPipe(
        ctypes.c_void_p(handle),
        None,
        0,
        None,
        ctypes.byref(available),
        None,
    )
    if not ok:
        error = ctypes.get_last_error()
        if error:
            raise OSError(error, "PeekNamedPipe failed")
        return 0
    return int(available.value)


class EmbeddedMediaPlayer:
    """Crash-isolated mpv player embedded into a Windows HWND."""

    def __init__(
        self,
        path: Path,
        *,
        window_id: int | None = None,
        audio_only: bool = False,
    ) -> None:
        self.path = Path(path)
        self.window_id = int(window_id) if window_id else None
        self.audio_only = bool(audio_only)

        self._lock = threading.RLock()
        self._closed = False
        self._ready = threading.Event()
        self._latest_position: float | None = None
        self._paused = True
        self._fatal_error = ""
        self._last_command_error = ""
        self._last_status = ""
        self._ipc = None
        self._commands: "queue.Queue[list[Any]]" = queue.Queue()

        pipe_name = f"media_downloader_mpv_{os.getpid()}_{uuid.uuid4().hex}"
        self._ipc_path = rf"\\.\pipe\{pipe_name}"

        command = [
            str(_mpv_executable()),
            "--no-config",
            "--idle=yes",
            "--keep-open=yes",
            "--pause=yes",
            "--input-terminal=no",
            "--osc=no",
            "--osd-bar=no",
            "--terminal=no",
            "--msg-level=all=no",
            f"--input-ipc-server={self._ipc_path}",
        ]

        if self.audio_only:
            command += ["--video=no", "--vo=null"]
        elif self.window_id is not None:
            command += [
                f"--wid={self.window_id}",
                "--force-window=yes",
                "--hwdec=auto-safe",
                "--keepaspect=yes",
                "--background-color=#030812",
            ]
        else:
            command += ["--vo=null"]

        LOGGER.info("Starting mpv source=%s audio_only=%s window_id=%s", self.path, self.audio_only, self.window_id)
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )

        self._ipc_thread = threading.Thread(target=self._ipc_worker, daemon=True)
        self._ipc_thread.start()

    @property
    def available(self) -> bool:
        return not self._closed and self._process.poll() is None

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.available and not self.fatal_error

    @property
    def fatal_error(self) -> str:
        with self._lock:
            return self._fatal_error

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._fatal_error or self._last_command_error

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        self._ready.wait(max(0.0, timeout))
        return self.ready

    def play(self) -> None:
        self._command(["set_property", "pause", False])

    def pause(self) -> None:
        self._command(["set_property", "pause", True], tolerate_dead=True)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def seek(self, seconds: float) -> None:
        self._command(["set_property", "time-pos", max(0.0, float(seconds))])

    def position(self) -> float | None:
        self._check_alive()
        with self._lock:
            return self._latest_position

    def set_volume(self, value: float) -> None:
        self._command(
            ["set_property", "volume", max(0.0, min(200.0, float(value) * 100.0))],
            tolerate_dead=True,
        )

    def set_mute(self, muted: bool) -> None:
        self._command(["set_property", "mute", bool(muted)], tolerate_dead=True)

    def set_rate(self, value: float) -> bool:
        self._command(
            ["set_property", "speed", max(0.25, min(4.0, float(value)))],
            tolerate_dead=True,
        )
        return True

    def set_video_transform(
        self,
        *,
        crop: tuple[int, int, int, int] | None,
        rotate_degrees: int,
    ) -> None:
        if self.audio_only:
            return

        if crop:
            x, y, width, height = crop
            self._command(
                ["vf", "set", f"crop={int(width)}:{int(height)}:{int(x)}:{int(y)}"],
                tolerate_dead=True,
            )
        else:
            self._command(["vf", "clr"], tolerate_dead=True)

        self._command(
            ["set_property", "video-rotate", int(rotate_degrees) % 360],
            tolerate_dead=True,
        )

    def next_frame(self, force_refresh: bool = False) -> None:
        del force_refresh
        self._check_alive()
        return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

        try:
            self._command(["quit"], tolerate_dead=True)
            self._process.wait(timeout=1.2)
        except Exception:
            try:
                self._process.terminate()
            except Exception:
                pass

        with self._lock:
            self._closed = True

        try:
            if self._ipc is not None:
                self._ipc.close()
        except Exception:
            pass

        try:
            if self._process.poll() is None:
                self._process.kill()
        except Exception:
            pass

    def _ipc_worker(self) -> None:
        deadline = time.monotonic() + 6.0
        last_error = ""
        while time.monotonic() < deadline and self.available:
            try:
                self._ipc = open(self._ipc_path, "r+b", buffering=0)
                break
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.05)

        if self._ipc is None:
            with self._lock:
                self._fatal_error = last_error or "Could not connect to mpv IPC."
            LOGGER.error("mpv IPC connection failed source=%s error=%s", self.path, self._fatal_error)
            self._ready.set()
            return

        self._commands.put(["observe_property", 1, "time-pos"])
        self._commands.put(["observe_property", 2, "pause"])
        self._commands.put(["observe_property", 3, "eof-reached"])
        self._commands.put(["loadfile", str(self.path), "replace"])

        buffer = b""
        try:
            while not self._closed and self._process.poll() is None:
                while True:
                    try:
                        command = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    self._write_command(command)

                available = _peek_pipe_bytes(self._ipc)
                if available > 0:
                    chunk = self._ipc.read(min(available, 65536))
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        if not raw.strip():
                            continue
                        try:
                            message = json.loads(raw.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            continue
                        self._handle_message(message)
                else:
                    time.sleep(0.008)
        except Exception as exc:
            with self._lock:
                if not self._fatal_error:
                    self._fatal_error = str(exc)
            LOGGER.exception("mpv IPC worker failed source=%s", self.path)
        finally:
            if not self._ready.is_set():
                self._ready.set()

    def _write_command(self, command: list[Any]) -> None:
        if self._ipc is None:
            raise PlayerUnavailableError("mpv IPC is not connected yet.")
        payload = (json.dumps({"command": command}, separators=(",", ":")) + "\n").encode("utf-8")
        self._ipc.write(payload)

    def _handle_message(self, message: dict[str, Any]) -> None:
        event = str(message.get("event") or "")

        if event == "file-loaded":
            with self._lock:
                self._last_status = "paused"
            self._ready.set()
            return

        if event == "end-file":
            reason = str(message.get("reason") or "")
            if reason == "error":
                with self._lock:
                    self._fatal_error = "mpv could not decode the selected media."
                LOGGER.error("mpv decode failed source=%s", self.path)
            return

        if event == "property-change":
            name = str(message.get("name") or "")
            data = message.get("data")
            if name == "time-pos":
                try:
                    value = float(data)
                except (TypeError, ValueError):
                    return
                with self._lock:
                    self._latest_position = max(0.0, value)
            elif name == "pause":
                paused = bool(data)
                with self._lock:
                    self._paused = paused
                    self._last_status = "paused" if paused else "playing"
            return

        error = message.get("error")
        if error not in (None, "success"):
            with self._lock:
                self._last_command_error = str(error)
            LOGGER.warning("mpv command warning source=%s error=%s", self.path, error)

    def _check_alive(self) -> None:
        if self._closed:
            raise PlayerUnavailableError("Embedded player is closed.")
        code = self._process.poll()
        if code is not None:
            detail = self.fatal_error.strip() or self.last_error.strip()
            suffix = f" {detail}" if detail else ""
            raise PlayerUnavailableError(f"mpv playback process stopped (exit {code}).{suffix}")

    def _command(self, command: list[Any], tolerate_dead: bool = False) -> None:
        if not tolerate_dead:
            self._check_alive()
        elif self._process.poll() is not None or self._closed:
            return
        self._commands.put(command)
