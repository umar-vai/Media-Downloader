"""Find the half-downloaded leftovers of one download so they can be cleaned up with it.

yt-dlp writes ``<title>.<ext>.part`` (and ``.ytdl``) while a download runs and, for HLS/DASH or
separate video and audio streams, ``<title>.f137.mp4``, ``<title>.f137.mp4.part-Frag12`` and so on.
A cancelled or failed download can leave many of these behind. They are never a finished file, so
removing the download from the list should remove them too.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# Same normalisation the download engine applies to the title when it picks the file name.
_EXTENSIONS = re.compile(r"\.(mp3|m4a|mp4|webm|mkv|mov|wav|flac|opus|ogg)$", re.I)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SPACES = re.compile(r"\s+")
# yt-dlp's per-stream pieces, FFmpeg temporary outputs, and direct HTTP resume files.
_STREAM_FILE = r"\.f(?:\d|hls|dash|http)[^.]*\.\w+"
_DOWNLOAD_SUFFIX = r"(?:\.part(?:-frag\d+)?|\.ytdl|\.mdpart|-frag\d+|\.unpng)"
_MERGE_SUFFIX = r"\.temp\.(?:mp4|m4a|webm|mkv|mov|mp3|opus|wav|flac)"
_ANY_LEFTOVER = re.compile(rf"(?:{_DOWNLOAD_SUFFIX}|{_STREAM_FILE}|{_MERGE_SUFFIX})$", re.I)


# Private per-download folder the engine stages everything in until the file is finished.
WORKSPACE_PREFIX = ".media-downloader-"


def _is_workspace(path: Path) -> bool:
    return path.name.startswith(WORKSPACE_PREFIX) and path.is_dir()


def _workspace_title(path: Path) -> str:
    try:
        return (path / ".title").read_text(encoding="utf-8")
    except OSError:
        return ""


def remove_path(path: Path) -> None:
    """Delete a leftover file or a whole workspace folder."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def path_size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return path.stat().st_size
    except OSError:
        return 0


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
            elif _is_workspace(path) and stem_for(_workspace_title(path)) == stem:
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
            remove_path(path)
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
                if path in keep:
                    continue
                if _is_workspace(path) or (_ANY_LEFTOVER.search(path.name) and path.is_file()):
                    found.append(path)
        except OSError:
            continue
    return found
