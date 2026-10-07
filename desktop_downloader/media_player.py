from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

try:
    from ffpyplayer.player import MediaPlayer
except Exception as exc:  # pragma: no cover - exercised by runtime fallback
    MediaPlayer = None  # type: ignore[assignment]
    PLAYER_IMPORT_ERROR = str(exc)
else:
    PLAYER_IMPORT_ERROR = ""


class PlayerUnavailableError(RuntimeError):
    pass


class EmbeddedMediaPlayer:
    """Small ffpyplayer wrapper for Tk-friendly polling playback."""

    def __init__(self, path: Path) -> None:
        if MediaPlayer is None:
            raise PlayerUnavailableError(
                "Embedded playback engine is unavailable"
                + (f": {PLAYER_IMPORT_ERROR}" if PLAYER_IMPORT_ERROR else ".")
            )
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._player = MediaPlayer(
            str(self.path),
            ff_opts={
                "sync": "audio",
                "out_fmt": "rgb24",
            },
        )
        self._player.set_pause(True)

    @property
    def available(self) -> bool:
        return not self._closed

    def play(self) -> None:
        with self._lock:
            self._ensure_open()
            self._player.set_pause(False)

    def pause(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._player.set_pause(True)

    def is_paused(self) -> bool:
        with self._lock:
            if self._closed:
                return True
            try:
                return bool(self._player.get_pause())
            except Exception:
                return True

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._ensure_open()
            self._player.seek(max(0.0, float(seconds)), relative=False, accurate=True)

    def position(self) -> float | None:
        with self._lock:
            if self._closed:
                return None
            try:
                value = self._player.get_pts()
            except Exception:
                return None
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None

    def set_volume(self, value: float) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._player.set_volume(max(0.0, min(1.0, float(value))))
            except Exception:
                pass

    def next_frame(self) -> tuple[bytes, tuple[int, int], float | None, Any] | None:
        """Return RGB bytes, size, PTS and ffpyplayer's scheduling value."""
        with self._lock:
            self._ensure_open()
            frame, schedule = self._player.get_frame()
            if frame is None:
                return None
            image, pts = frame
            width, height = image.get_size()
            planes = image.to_bytearray()
            if not planes:
                return None
            data = bytes(planes[0])
            return data, (int(width), int(height)), float(pts), schedule

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._player.set_pause(True)
            except Exception:
                pass
            try:
                self._player.close_player()
            except Exception:
                pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise PlayerUnavailableError("Embedded player is closed.")
