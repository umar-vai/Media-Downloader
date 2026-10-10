from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 38478
CORE_HOST = "127.0.0.1"
CORE_PORT = 38477
CORE_URL = f"http://{CORE_HOST}:{CORE_PORT}"
MUTEX_NAME = "Local\\MediaDownloaderCoreAgent"


def executable_path() -> Path:
    return Path(sys.executable).resolve()


def is_packaged_agent() -> bool:
    return bool(getattr(sys, "frozen", False)) and executable_path().name.lower() == "mediadownloadercore.exe"


def updater_helper_path() -> Path:
    return executable_path().with_name("MediaDownloaderCoreUpdater.exe")


def previous_core_path() -> Path:
    target = executable_path()
    return target.with_suffix(target.suffix + ".previous")


def rollback_available() -> bool:
    return is_packaged_agent() and previous_core_path().is_file()


def can_self_update() -> bool:
    return is_packaged_agent() and updater_helper_path().is_file()


def send_control(command: str, **payload: Any) -> dict[str, Any]:
    request = {"command": str(command), **payload}
    raw = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    with socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=2.0) as sock:
        sock.sendall(raw)
        sock.settimeout(3.0)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    if not chunks:
        raise RuntimeError("Media Downloader agent did not respond.")
    response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "Agent command failed."))
    return response
