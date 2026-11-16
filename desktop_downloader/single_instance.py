"""Prevent accidental duplicate Windows Media Downloader tray processes.

The mutex is held by this GUI process until process exit; self-test mode skips
the mutex so CI can run without affecting an installed app.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path
from typing import Callable

_MUTEX_NAME = r"Local\TeamFahad.MediaDownloader.SingleInstance"
# A second launch (taskbar, Start menu, desktop shortcut) sets this event; the running copy
# waits on it and brings its window back from the tray instead of starting again.
_SHOW_EVENT_NAME = r"Local\TeamFahad.MediaDownloader.ShowWindow"
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF
_ASFW_ANY = 0xFFFFFFFF
_mutex_handle: int | None = None
_show_event: int | None = None


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


def signal_existing_instance() -> bool:
    """Ask the running copy to show its window. False when it cannot be reached (older version)."""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.SetEvent.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, _SHOW_EVENT_NAME)
        if not handle:
            return False
        try:
            # This process was just started by the user, so it may hand the foreground to the
            # running copy; otherwise Windows would only flash its taskbar button.
            user32 = ctypes.WinDLL("user32")
            user32.AllowSetForegroundWindow.argtypes = [ctypes.c_ulong]
            user32.AllowSetForegroundWindow(_ASFW_ANY)
        except Exception:
            pass
        ok = bool(kernel32.SetEvent(handle))
        kernel32.CloseHandle(handle)
        return ok
    except Exception:
        return False


def listen_for_show_requests(on_show: Callable[[], None]) -> bool:
    """Call ``on_show`` (from a background thread) each time another launch asks for the window."""
    global _show_event
    if os.name != "nt" or _show_event:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        handle = kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)  # auto-reset
        if not handle:
            return False
    except Exception:
        return False
    _show_event = handle

    def wait() -> None:
        while True:
            try:
                result = kernel32.WaitForSingleObject(handle, _INFINITE)
            except Exception:
                return
            if result != _WAIT_OBJECT_0:
                return
            try:
                on_show()
            except Exception:
                pass

    threading.Thread(target=wait, name="show-window-listener", daemon=True).start()
    return True


def bring_to_front(hwnd: int) -> None:
    """Restore and focus a top-level window (Windows only, best effort)."""
    if os.name != "nt" or not hwnd:
        return
    try:
        user32 = ctypes.WinDLL("user32")
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


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
