from __future__ import annotations

import os
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MediaDownloaderCore"


def is_windows() -> bool:
    return os.name == "nt"


def executable_path() -> Path:
    return Path(sys.executable).resolve()


def can_manage_startup() -> bool:
    return is_windows() and bool(getattr(sys, "frozen", False)) and executable_path().suffix.lower() == ".exe"


def set_launch_at_login(enabled: bool) -> None:
    if not is_windows():
        raise RuntimeError("Launch at sign-in is only supported on Windows.")
    if not can_manage_startup():
        raise RuntimeError("Launch at sign-in becomes available in the packaged Local Core.")

    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            command = f'"{executable_path()}" --background'
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def launch_at_login_enabled() -> bool:
    if not is_windows():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
        return str(executable_path()).lower() in str(value).lower()
    except (FileNotFoundError, OSError):
        return False
