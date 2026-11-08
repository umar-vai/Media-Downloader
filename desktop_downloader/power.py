"""Keep downloads running at full speed while the window is minimized or hidden in the tray.

Windows 11 can throttle ("efficiency mode"/EcoQoS) a process whose windows are minimized, which
slows or stalls background downloads. A download manager opts out of that, and also asks Windows
not to go to sleep while transfers are active.
"""

from __future__ import annotations

import os

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_awake = False


def disable_power_throttling() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class PowerThrottlingState(ctypes.Structure):
            _fields_ = [
                ("Version", wintypes.ULONG),
                ("ControlMask", wintypes.ULONG),
                ("StateMask", wintypes.ULONG),
            ]

        # Control both EXECUTION_SPEED (0x1) and IGNORE_TIMER_RESOLUTION (0x4), state 0 = "never throttle".
        state = PowerThrottlingState(1, 0x1 | 0x4, 0)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        ok = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)
        )
        return bool(ok)
    except Exception:
        return False


def keep_system_awake(active: bool) -> None:
    """Prevent system sleep (not display sleep) only while downloads are running."""
    global _awake
    if os.name != "nt" or active == _awake:
        return
    try:
        import ctypes

        flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if active else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        _awake = active
    except Exception:
        pass
