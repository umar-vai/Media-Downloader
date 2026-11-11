"""Prevent accidental duplicate Windows Media Downloader tray processes.

The mutex is held by this GUI process until process exit; self-test mode skips
the mutex so CI can run without affecting an installed app.
"""
from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

_MUTEX_NAME = r"Local\TeamFahad.MediaDownloader.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle: int | None = None


def claim_single_instance() -> bool:
    """Return False when another GUI instance is already running on Windows."""
    global _mutex_handle
    if os.name != "nt":
        return True
    if _mutex_handle:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    handle = create(None, False, _MUTEX_NAME)
    if not handle:
        # Never make a desktop application unusable due to unusual mutex errors.
        return True
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _mutex_handle = handle
    return True


def alert_existing_instance() -> None:
    """Show a short instruction instead of starting another hidden tray icon."""
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(
            None,
            "Media Downloader is already running.\n\n"
            "Open it from its icon in the system tray (near the clock).",
            "Media Downloader",
            0x40,
        )


def cleanup_stale_updater_copies(directory: Path, *, min_age_seconds: int = 600) -> None:
    """Best-effort removal of unique updater copies left after earlier handoffs.

    A running updater EXE stays locked on Windows and will not be removed.
    Do not touch current or recently started updater processes.
    """
    try:
        if not directory.is_dir():
            return
        cutoff = time.time() - max(60, min_age_seconds)
        for item in directory.glob("MediaDownloaderUpdater-run-*.exe"):
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
            except OSError:
                pass
    except OSError:
        pass
