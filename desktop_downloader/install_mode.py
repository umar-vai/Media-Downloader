from __future__ import annotations

import sys
from pathlib import Path

INSTALL_MARKER = ".media_downloader_installed"


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def install_marker_path() -> Path:
    return executable_dir() / INSTALL_MARKER


def is_installed_mode() -> bool:
    return bool(getattr(sys, "frozen", False) and install_marker_path().exists())


def install_mode_name() -> str:
    if not getattr(sys, "frozen", False):
        return "source"
    return "installer" if is_installed_mode() else "portable"
