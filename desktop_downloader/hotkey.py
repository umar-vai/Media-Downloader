"""Global hotkey (Windows): works while the window is hidden in the tray."""

from __future__ import annotations

import os
import threading
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class GlobalHotkey:
    """Registers one system-wide hotkey on a background thread and calls ``callback`` on press."""

    def __init__(self, callback: Callable[[], None], *, modifiers: int, vk: int, hotkey_id: int = 0x4D44) -> None:
        self._callback = callback
        self._modifiers = modifiers | MOD_NOREPEAT
        self._vk = vk
        self._id = hotkey_id
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self) -> bool:
        if os.name != "nt" or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self.registered

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(None, self._id, self._modifiers, self._vk))
        self._ready.set()
        if not self.registered:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == self._id:
                    try:
                        self._callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, self._id)

    def stop(self) -> None:
        if self._thread is None or not self._thread_id:
            return
        try:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        self._thread = None
        self.registered = False
