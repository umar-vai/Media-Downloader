"""Start Media Downloader with Windows (per-user Run key, no admin rights needed)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MediaDownloader"
MINIMIZED_FLAG = "--minimized"


def supported() -> bool:
    return os.name == "nt"


def launch_command() -> str:
    """Command line written to the Run key: the packaged exe, or pythonw + launcher.py from source."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" {MINIMIZED_FLAG}'
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    if pythonw.exists():
        python = pythonw
    launcher = Path(__file__).resolve().with_name("launcher.py")
    return f'"{python}" "{launcher}" {MINIMIZED_FLAG}'


def is_enabled() -> bool:
    if not supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(str(value).strip())
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise RuntimeError("Starting with Windows is only available on Windows.")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def started_minimized(argv: list[str] | None = None) -> bool:
    return MINIMIZED_FLAG in (sys.argv if argv is None else argv)
