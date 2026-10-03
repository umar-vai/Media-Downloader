from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import Tk, messagebox

API_URL = "https://api.github.com/repos/umar-vai/Media-Downloader/releases/latest"
RELEASE_URL = "https://github.com/umar-vai/Media-Downloader/releases/latest"
USER_AGENT = "MediaDownloader-LegacyBridge/1.0"
ASSET_NAME = "MediaDownloader.exe"
CHECKSUM_NAME = f"{ASSET_NAME}.sha256"


def request_bytes(url: str, attempts: int = 5, timeout: int = 30) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Connection": "close"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(10, 1.5 * attempt * attempt))
    raise RuntimeError(f"Could not connect to the update server: {last_error}")


def download_file(url: str, destination: Path, attempts: int = 6) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            destination.unlink(missing_ok=True)
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/octet-stream",
                    "Connection": "close",
                },
            )
            with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            if destination.exists() and destination.stat().st_size > 0:
                return
        except Exception as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
        if attempt < attempts:
            time.sleep(min(12, 1.5 * attempt * attempt))

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        result = subprocess.run(
            [
                curl,
                "-L",
                "--fail",
                "--retry",
                "6",
                "--retry-all-errors",
                "--connect-timeout",
                "20",
                "--max-time",
                "900",
                "-A",
                USER_AGENT,
                "-o",
                str(destination),
                url,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True,
            text=True,
            timeout=930,
        )
        if result.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
            return
        last_error = RuntimeError((result.stderr or result.stdout or "curl download failed")[-500:])

    raise RuntimeError(f"The update file could not be downloaded after multiple attempts: {last_error}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    root = Tk()
    root.withdraw()

    current_exe = Path(sys.executable).resolve()
    work_dir = Path(tempfile.gettempdir()) / "MediaDownloaderUpdateBridge"
    work_dir.mkdir(parents=True, exist_ok=True)
    new_exe = work_dir / "MediaDownloader.new.exe"
    batch = work_dir / "finish-update.cmd"

    try:
        messagebox.showinfo(
            "Media Downloader",
            "Media Downloader is completing a one-time updater migration.\n\n"
            "This may take a minute. The app will reopen automatically when finished.",
        )

        payload = json.loads(request_bytes(API_URL).decode("utf-8"))
        assets = {
            str(asset.get("name") or ""): str(asset.get("browser_download_url") or "")
            for asset in payload.get("assets") or []
            if isinstance(asset, dict)
        }
        exe_url = assets.get(ASSET_NAME, "")
        checksum_url = assets.get(CHECKSUM_NAME, "")
        if not exe_url or not checksum_url:
            raise RuntimeError("The latest release is missing MediaDownloader.exe or its checksum.")

        checksum_text = request_bytes(checksum_url).decode("utf-8", errors="replace")
        expected = checksum_text.strip().split()[0].lower()
        if len(expected) != 64:
            raise RuntimeError("The release checksum is invalid.")

        download_file(exe_url, new_exe)
        if sha256(new_exe).lower() != expected:
            raise RuntimeError("Downloaded update verification failed.")

        old_exe = Path(str(current_exe) + ".old")
        batch.write_text(
            "@echo off\r\n"
            "ping 127.0.0.1 -n 3 >nul\r\n"
            f'copy /Y "{current_exe}" "{old_exe}" >nul 2>nul\r\n'
            f'copy /Y "{new_exe}" "{current_exe}" >nul\r\n'
            "if errorlevel 1 (\r\n"
            f'  copy /Y "{old_exe}" "{current_exe}" >nul 2>nul\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            f'del /Q "{old_exe}" >nul 2>nul\r\n'
            f'start "" "{current_exe}"\r\n'
            f'del /Q "{new_exe}" >nul 2>nul\r\n'
            'del /Q "%~f0" >nul 2>nul\r\n',
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "/min", str(batch)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        root.destroy()
    except Exception as exc:
        open_release = messagebox.askyesno(
            "Media Downloader",
            "The automatic update could not finish yet. Your current app is still safe.\n\n"
            f"{exc}\n\nOpen the latest Media Downloader release in your browser?",
        )
        if open_release:
            os.startfile(RELEASE_URL)
        root.destroy()


if __name__ == "__main__":
    main()
