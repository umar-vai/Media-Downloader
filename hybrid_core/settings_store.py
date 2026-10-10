from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


ALLOWED_VIDEO_QUALITIES = {"Best available", "1080p", "720p", "480p", "360p"}
ALLOWED_AUDIO_FORMATS = {"MP3", "M4A", "WAV"}
ALLOWED_AUDIO_QUALITIES = {"128", "192", "256", "320"}
ALLOWED_UPDATE_CHANNELS = {"stable", "beta"}


def default_settings(default_download_dir: Path) -> dict[str, Any]:
    return {
        "download_dir": str(Path(default_download_dir).expanduser()),
        "default_mode": "Video",
        "video_quality": "720p",
        "audio_format": "MP3",
        "audio_quality": "192",
        "max_concurrent_downloads": 3,
        "auto_check_core_updates": True,
        "update_channel": "stable",
        "open_browser_on_start": True,
    }


def normalize_settings(payload: dict[str, Any] | None, default_download_dir: Path) -> dict[str, Any]:
    settings = default_settings(default_download_dir)
    if isinstance(payload, dict):
        settings.update(payload)

    download_dir = str(settings.get("download_dir") or "").strip()
    settings["download_dir"] = download_dir or str(Path(default_download_dir).expanduser())

    mode = str(settings.get("default_mode") or "Video")
    settings["default_mode"] = mode if mode in {"Video", "Audio"} else "Video"

    quality = str(settings.get("video_quality") or "720p")
    settings["video_quality"] = quality if quality in ALLOWED_VIDEO_QUALITIES else "720p"

    audio_format = str(settings.get("audio_format") or "MP3").upper()
    settings["audio_format"] = audio_format if audio_format in ALLOWED_AUDIO_FORMATS else "MP3"

    audio_quality = str(settings.get("audio_quality") or "192")
    settings["audio_quality"] = audio_quality if audio_quality in ALLOWED_AUDIO_QUALITIES else "192"

    try:
        concurrency = int(settings.get("max_concurrent_downloads") or 3)
    except (TypeError, ValueError):
        concurrency = 3
    settings["max_concurrent_downloads"] = max(1, min(6, concurrency))

    settings["auto_check_core_updates"] = bool(settings.get("auto_check_core_updates", True))
    settings["open_browser_on_start"] = bool(settings.get("open_browser_on_start", True))

    channel = str(settings.get("update_channel") or "stable").lower()
    settings["update_channel"] = channel if channel in ALLOWED_UPDATE_CHANNELS else "stable"
    return settings


class SettingsStore:
    def __init__(self, path: Path, *, default_download_dir: Path) -> None:
        self.path = Path(path)
        self.default_download_dir = Path(default_download_dir)
        self._lock = threading.RLock()
        self._settings = self._load()
        self.save(self._settings)

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            payload = None
        return normalize_settings(payload if isinstance(payload, dict) else None, self.default_download_dir)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def update(self, patch: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            before = dict(self._settings)
            merged = dict(before)
            merged.update({key: value for key, value in patch.items() if value is not None})
            normalized = normalize_settings(merged, self.default_download_dir)
            self._settings = normalized
            self.save(normalized)

        restart_required = any(
            before.get(key) != normalized.get(key)
            for key in ("max_concurrent_downloads", "open_browser_on_start")
        )
        return dict(normalized), restart_required

    def save(self, settings: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
