"""Find the half-downloaded leftovers of one download so they can be cleaned up with it.

yt-dlp writes ``<title>.<ext>.part`` (and ``.ytdl``) while a download runs and, for HLS/DASH or
separate video and audio streams, ``<title>.f137.mp4``, ``<title>.f137.mp4.part-Frag12`` and so on.
A cancelled or failed download can leave many of these behind. They are never a finished file, so
removing the download from the list should remove them too.
"""

from __future__ import annotations

import re
from pathlib import Path

# Same normalisation the download engine applies to the title when it picks the file name.
_EXTENSIONS = re.compile(r"\.(mp3|m4a|mp4|webm|mkv|mov|wav|flac|opus|ogg)$", re.I)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SPACES = re.compile(r"\s+")
# yt-dlp's per-stream pieces, FFmpeg temporary outputs, and direct HTTP resume files.
_STREAM_FILE = r"\.f(?:\d|hls|dash|http)[^.]*\.\w+"
_DOWNLOAD_SUFFIX = r"(?:\.part(?:-frag\d+)?|\.ytdl|\.mdpart(?:\.json)?|-frag\d+|\.unpng)"
_MERGE_SUFFIX = r"\.temp\.(?:mp4|m4a|webm|mkv|mov|mp3|opus|wav|flac)"
_ANY_LEFTOVER = re.compile(rf"(?:{_DOWNLOAD_SUFFIX}|{_STREAM_FILE}|{_MERGE_SUFFIX})$", re.I)


def stem_for(name: str) -> str:
    text = _EXTENSIONS.sub("", str(name or "").strip())
    text = _UNSAFE.sub("_", text)
    text = _SPACES.sub(" ", text).strip(" ._")
    return text[:150].strip()


def _is_leftover(rest: str) -> bool:
    """``rest`` is whatever follows the title in the file name, starting with a dot."""
    return bool(_ANY_LEFTOVER.search(rest))


def find_partial_files(directory: str | Path, name: str) -> list[Path]:
    """Unfinished files that belong to the download called ``name`` inside ``directory``.

    The title must match exactly (optionally followed by the " (2)" the engine adds to avoid a
    clash), so a download called "Video" never touches the files of "Video 2".
    """
    stem = stem_for(name)
    if not stem or not directory:
        return []
    pattern = re.compile(re.escape(stem) + r"(?: \(\d+\))?(?P<rest>\..+)$")
    found: list[Path] = []
    try:
        for path in Path(directory).iterdir():
            match = pattern.fullmatch(path.name)
            if match and _is_leftover(match.group("rest")) and path.is_file():
                found.append(path)
    except OSError:
        pass
    return found


def delete_partial_files(directory: str | Path, name: str) -> tuple[int, list[str]]:
    """Delete the leftovers; returns how many went and a message per file that could not."""
    removed = 0
    problems: list[str] = []
    for path in find_partial_files(directory, name):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            problems.append(f"{path.name}: {exc}")
    return removed, problems


def scan_partial_files(directories: list[str | Path], keep: set[Path] | None = None) -> list[Path]:
    """Every yt-dlp leftover in the given folders (not subfolders), except the paths in ``keep``."""
    keep = keep or set()
    found: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        try:
            folder = Path(directory).resolve()
            if folder in seen or not folder.is_dir():
                continue
            seen.add(folder)
            for path in folder.iterdir():
                if _ANY_LEFTOVER.search(path.name) and path.is_file() and path not in keep:
                    found.append(path)
        except OSError:
            continue
    return found
