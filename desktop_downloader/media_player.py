from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PlayerUnavailableError(RuntimeError):
    pass


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _worker_command(path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        worker = _resource_path("MediaPlaybackWorker.exe")
        if not worker.exists():
            raise PlayerUnavailableError(f"Playback worker is missing: {worker}")
        return [str(worker), str(path)]

    worker_py = Path(__file__).resolve().with_name("media_player_worker.py")
    if not worker_py.exists():
        raise PlayerUnavailableError(f"Playback worker source is missing: {worker_py}")
    return [sys.executable, str(worker_py), str(path)]


class EmbeddedMediaPlayer:
    """Crash-isolated playback client.

    ffpyplayer runs in a child process. If the native decoder crashes,
    the editor process stays alive and can fall back to frame preview.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._ready = threading.Event()
        self._latest_frame: tuple[bytes, float | None, float | None] | None = None
        self._latest_position: float | None = None
        self._last_error = ""
        self._last_status = ""
        self._stderr_tail = ""
        self._process = subprocess.Popen(
            _worker_command(self.path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    @property
    def available(self) -> bool:
        return not self._closed and self._process.poll() is None

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.available

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error or self._stderr_tail

    def wait_until_ready(self, timeout: float = 3.0) -> bool:
        self._ready.wait(max(0.0, timeout))
        return self.ready

    def play(self) -> None:
        self._send({"cmd": "play"})

    def pause(self) -> None:
        if self.available:
            self._send({"cmd": "pause"}, tolerate_dead=True)

    def is_paused(self) -> bool:
        with self._lock:
            return self._last_status != "playing"

    def seek(self, seconds: float) -> None:
        self._send({"cmd": "seek", "seconds": max(0.0, float(seconds))})

    def position(self) -> float | None:
        self._check_alive()
        with self._lock:
            return self._latest_position

    def set_volume(self, value: float) -> None:
        self._send(
            {"cmd": "volume", "value": max(0.0, min(1.0, float(value)))},
            tolerate_dead=True,
        )

    def set_rate(self, value: float) -> bool:
        self._send(
            {"cmd": "rate", "value": max(0.25, min(4.0, float(value)))},
            tolerate_dead=True,
        )
        return True

    def next_frame(self, force_refresh: bool = False) -> tuple[bytes, float | None, float | None] | None:
        del force_refresh
        self._check_alive()
        with self._lock:
            frame = self._latest_frame
            self._latest_frame = None
            return frame

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._send({"cmd": "close"}, tolerate_dead=True)
        except Exception:
            pass
        try:
            self._process.wait(timeout=1.5)
        except Exception:
            try:
                self._process.terminate()
            except Exception:
                pass
        try:
            if self._process.poll() is None:
                self._process.kill()
        except Exception:
            pass

    def _check_alive(self) -> None:
        if self._closed:
            raise PlayerUnavailableError("Embedded player is closed.")
        code = self._process.poll()
        if code is not None:
            detail = self.last_error.strip()
            suffix = f" {detail}" if detail else ""
            raise PlayerUnavailableError(
                f"Playback worker stopped unexpectedly (exit {code}).{suffix}"
            )

    def _send(self, payload: dict[str, Any], tolerate_dead: bool = False) -> None:
        if not tolerate_dead:
            self._check_alive()
        elif self._process.poll() is not None:
            return
        stream = self._process.stdin
        if stream is None:
            if tolerate_dead:
                return
            raise PlayerUnavailableError("Playback worker command pipe is unavailable.")
        try:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
            stream.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            if tolerate_dead:
                return
            raise PlayerUnavailableError(f"Playback worker command failed: {exc}") from exc

    def _read_loop(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = str(message.get("type") or "")
                if msg_type == "ready":
                    with self._lock:
                        self._last_status = "paused"
                    self._ready.set()
                elif msg_type == "status":
                    with self._lock:
                        self._last_status = str(message.get("state") or "")
                elif msg_type == "position":
                    try:
                        value = float(message.get("pts"))
                    except (TypeError, ValueError):
                        continue
                    with self._lock:
                        self._latest_position = max(0.0, value)
                elif msg_type == "frame":
                    encoded = message.get("jpeg")
                    if not isinstance(encoded, str):
                        continue
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except Exception:
                        continue
                    pts = message.get("pts")
                    delay = message.get("delay")
                    try:
                        pts_value = float(pts) if pts is not None else None
                    except (TypeError, ValueError):
                        pts_value = None
                    try:
                        delay_value = float(delay) if delay is not None else None
                    except (TypeError, ValueError):
                        delay_value = None
                    with self._lock:
                        self._latest_frame = (data, pts_value, delay_value)
                        if pts_value is not None:
                            self._latest_position = max(0.0, pts_value)
                elif msg_type == "error":
                    with self._lock:
                        self._last_error = str(message.get("message") or "Playback worker error")
        finally:
            if not self._ready.is_set():
                self._ready.set()

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        tail: list[str] = []
        try:
            for raw in stream:
                line = raw.strip()
                if line:
                    tail.append(line)
                    tail = tail[-12:]
                    with self._lock:
                        self._stderr_tail = " | ".join(tail)[-1200:]
        except Exception:
            pass
