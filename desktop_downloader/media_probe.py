"""Read basic facts about a downloaded file (resolution, codecs, bitrate, length) with the
bundled FFmpeg, for the History > Properties dialog."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BITRATE_RE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")
_VIDEO_RE = re.compile(r"Stream #[^\n]*?Video:\s*([A-Za-z0-9_]+)[^\n]*?\b(\d{2,5})x(\d{2,5})\b")
_AUDIO_RE = re.compile(r"Stream #[^\n]*?Audio:\s*([A-Za-z0-9_]+)")
_INPUT_RE = re.compile(r"Input #0,\s*([^,\n]+)")


def parse_ffmpeg_output(text: str) -> dict[str, Any]:
    info: dict[str, Any] = {}
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        info["duration_seconds"] = int(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    match = _BITRATE_RE.search(text)
    if match:
        info["bitrate_kbps"] = int(match.group(1))
    match = _VIDEO_RE.search(text)
    if match:
        codec, width, height = match.groups()
        info["video_codec"] = codec
        info["resolution"] = f"{width}x{height}"
    match = _AUDIO_RE.search(text)
    if match:
        info["audio_codec"] = match.group(1)
    match = _INPUT_RE.search(text)
    if match:
        info["container"] = match.group(1).strip()
    return info


def probe_media(path: Path, timeout: float = 15.0) -> dict[str, Any]:
    """Best effort: an empty dict when FFmpeg is unavailable or the file is unreadable."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        result = subprocess.run(
            [get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return {}
    return parse_ffmpeg_output((result.stderr or "") + (result.stdout or ""))
