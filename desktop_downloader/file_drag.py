"""Hand finished files to other programs, the way Explorer, IDM and NDM do.

``drag_files`` starts a real Windows drag with the shell's own data object, so the files can be
dropped on Telegram, WhatsApp, a browser upload box, a chat window or an Explorer folder, and the
cursor carries the usual file thumbnail while dragging. ``copy_files`` puts the same data object
on the clipboard so Ctrl+V pastes the files.

Only ``ctypes`` and the shell are used: no extra package. On other systems both functions do
nothing and return False.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Iterable

DROPEFFECT_COPY = 1
DROPEFFECT_LINK = 4


def available() -> bool:
    return os.name == "nt"


def usable_paths(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    """Absolute paths of existing files, in order, without duplicates."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        key = os.path.normcase(str(path))
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(str(path))
    return result


def common_parent(paths: list[str]) -> str | None:
    """The folder shared by every path, or None when they live in different folders."""
    parents = {os.path.normcase(str(Path(path).parent)) for path in paths}
    if len(parents) != 1:
        return None
    return str(Path(paths[0]).parent)


if os.name == "nt":
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]

    _IID_IDataObject = _GUID(0x0000010E, 0x0000, 0x0000, (ctypes.c_ubyte * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))

    _ole32 = ctypes.WinDLL("ole32")
    _shell32 = ctypes.WinDLL("shell32")

    _ole32.OleInitialize.argtypes = [ctypes.c_void_p]
    _ole32.OleInitialize.restype = ctypes.c_long
    _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _ole32.CoTaskMemFree.restype = None
    _ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    _ole32.OleSetClipboard.restype = ctypes.c_long
    _ole32.OleFlushClipboard.argtypes = []
    _ole32.OleFlushClipboard.restype = ctypes.c_long

    _shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    _shell32.SHParseDisplayName.restype = ctypes.c_long
    _shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
    _shell32.ILFindLastID.restype = ctypes.c_void_p
    _shell32.SHCreateDataObject.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    _shell32.SHCreateDataObject.restype = ctypes.c_long
    _shell32.SHDoDragDrop.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    _shell32.SHDoDragDrop.restype = ctypes.c_long

    _ole_ready = False

    def _ole_init() -> bool:
        global _ole_ready
        if not _ole_ready:
            # S_OK or S_FALSE (already initialised on this thread) are both fine.
            _ole_ready = _ole32.OleInitialize(None) >= 0
        return _ole_ready

    def _parse(path: str) -> int | None:
        pidl = ctypes.c_void_p()
        if _shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) < 0 or not pidl.value:
            return None
        return pidl.value

    def _release(pointer: int) -> None:
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        release(pointer)

    def _data_object(paths: list[str]) -> int | None:
        """A shell IDataObject for the files (CF_HDROP, shell ID list, drag image...)."""
        if not paths or not _ole_init():
            return None
        owned: list[int] = []
        try:
            items: list[int] = []
            for path in paths:
                pidl = _parse(path)
                if pidl is None:
                    return None
                owned.append(pidl)
                items.append(pidl)
            folder = None
            parent = common_parent(paths)
            if parent is not None:
                folder = _parse(parent)
                if folder is not None:
                    owned.append(folder)
                    # Children are the last item ID of each absolute ID list.
                    items = [_shell32.ILFindLastID(pidl) for pidl in items]
            # With no common folder the absolute ID lists are relative to the desktop (folder=None).
            array = (ctypes.c_void_p * len(items))(*items)
            result = ctypes.c_void_p()
            hr = _shell32.SHCreateDataObject(folder, len(items), array, None, ctypes.byref(_IID_IDataObject),
                                             ctypes.byref(result))
            if hr < 0 or not result.value:
                return None
            return result.value
        finally:
            for pidl in owned:
                _ole32.CoTaskMemFree(pidl)

    def drag_files(hwnd: int, paths: Iterable[str | os.PathLike[str]]) -> bool:
        """Run a modal file drag from ``hwnd``; returns True when the files were dropped somewhere."""
        files = usable_paths(paths)
        try:
            data = _data_object(files)
        except Exception:
            return False
        if data is None:
            return False
        try:
            effect = wintypes.DWORD(0)
            # pdsrc=None: the shell supplies the drop source and the drag image.
            hr = _shell32.SHDoDragDrop(hwnd, data, None, DROPEFFECT_COPY | DROPEFFECT_LINK, ctypes.byref(effect))
            return hr >= 0 and bool(effect.value)
        except Exception:
            return False
        finally:
            _release(data)

    def copy_files(paths: Iterable[str | os.PathLike[str]]) -> int:
        """Put the files on the clipboard (Ctrl+V pastes them); returns how many were copied."""
        files = usable_paths(paths)
        try:
            data = _data_object(files)
        except Exception:
            return 0
        if data is None:
            return 0
        try:
            if _ole32.OleSetClipboard(data) < 0:
                return 0
            _ole32.OleFlushClipboard()  # keep the files on the clipboard after the app quits
            return len(files)
        except Exception:
            return 0
        finally:
            _release(data)

else:

    def drag_files(hwnd: int, paths: Iterable[str | os.PathLike[str]]) -> bool:  # noqa: ARG001
        return False

    def copy_files(paths: Iterable[str | os.PathLike[str]]) -> int:  # noqa: ARG001
        return 0
