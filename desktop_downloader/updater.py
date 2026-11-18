from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def append_log(log_file: Path, message: str) -> None:
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def write_result(result_file: Path, status: str, version: str, message: str = "") -> None:
    try:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            json.dumps(
                {"status": status, "version": version, "message": message},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_process_exit(pid: int, timeout_seconds: int = 30) -> bool:
    if pid <= 0:
        return True

    if os.name != "nt":
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False

    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
        return result == WAIT_OBJECT_0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


INSTALLER_EXIT_CODES = {
    1: "Setup could not start.",
    2: "Setup was cancelled before installing.",
    3: "Setup hit a fatal error while preparing.",
    4: "Setup hit a fatal error while installing.",
    5: "A file of the program was still in use, so Setup could not replace it.",
    6: "Setup was stopped.",
    7: "Setup cannot continue (Windows needs a restart or a file is in use).",
    8: "Windows needs a restart to finish installing.",
}


def _is_inside(path: str, folder: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(str(folder))) + os.sep)
    except Exception:
        return False


def processes_in_folder(folder: Path) -> list[tuple[int, str]]:
    """Running processes whose program file lies inside ``folder`` (Windows only).

    The editor's mpv player, an ffmpeg started by the recorder or the editor, or a second
    copy of the app keep their EXE/DLL files locked; the installer then cannot replace them
    and stops with exit code 5.
    """
    if os.name != "nt":
        return []
    from ctypes import wintypes

    psapi = ctypes.windll.psapi
    kernel32 = ctypes.windll.kernel32
    size = 4096
    while True:
        pids = (wintypes.DWORD * size)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
            return []
        if needed.value < ctypes.sizeof(pids):
            break
        size *= 2
    found: list[tuple[int, str]] = []
    own = os.getpid()
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid == own:
            continue
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                if _is_inside(buffer.value, folder):
                    found.append((int(pid), buffer.value))
        finally:
            kernel32.CloseHandle(handle)
    return found


def stop_processes_in_folder(folder: Path, log_file: Path, timeout_seconds: float = 10.0) -> int:
    """Close every process still running from the install folder. Returns how many."""
    if os.name != "nt":
        return 0
    stopped = 0
    for pid, path in processes_in_folder(folder):
        handle = ctypes.windll.kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)  # TERMINATE | SYNCHRONIZE
        if not handle:
            append_log(log_file, f"Could not open process {pid} ({path}) to close it.")
            continue
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
            stopped += 1
            append_log(log_file, f"Closed process {pid} that was still using {path}.")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return stopped


def setup_log_errors(log_path: Path, limit: int = 6) -> str:
    """The error lines of an Inno Setup log (which file was locked, and why)."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    keys = ("error", "failed", "in use", "abort", "access is denied", "being used")
    hits = [line.strip() for line in lines if any(key in line.lower() for key in keys)]
    return "\n".join(hits[-limit:])


def launch_target(target: Path) -> subprocess.Popen[bytes]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=creationflags)


def replace_with_retry(source: Path, destination: Path, timeout_seconds: int = 15) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            os.replace(source, destination)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            time.sleep(0.4)
    if last_error:
        raise last_error
    raise RuntimeError("File replacement timed out.")


def restore_backup(target: Path, backup: Path, log_file: Path) -> None:
    try:
        if target.exists():
            target.unlink()
    except Exception:
        pass
    if backup.exists():
        replace_with_retry(backup, target)
        append_log(log_file, "Restored previous executable after failed update.")


def install_update(
    target: Path,
    source: Path,
    pid: int,
    version: str,
    result_file: Path,
    log_file: Path,
) -> int:
    append_log(log_file, f"Updater started for version {version}. Target={target}")

    if not source.exists():
        message = "Downloaded update file is missing."
        append_log(log_file, message)
        write_result(result_file, "failed", version, message)
        return 2

    if not wait_for_process_exit(pid):
        message = "The application did not close in time."
        append_log(log_file, message)
        write_result(result_file, "failed", version, message)
        return 3

    backup = target.with_name(f"{target.name}.backup")
    staged = target.with_name(f"{target.name}.new")

    try:
        staged.unlink(missing_ok=True)
        shutil.copy2(source, staged)
        if sha256_file(staged) != sha256_file(source):
            raise RuntimeError("Staged update verification failed.")

        backup.unlink(missing_ok=True)
        replace_with_retry(target, backup)
        replace_with_retry(staged, target)
        append_log(log_file, "Executable replaced successfully.")

        process = launch_target(target)
        time.sleep(5)
        if process.poll() is not None:
            raise RuntimeError("The updated application exited immediately after launch.")

        backup.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        write_result(result_file, "success", version, "Update installed successfully.")
        append_log(log_file, f"Update {version} completed successfully.")
        return 0
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        append_log(log_file, f"Update failed: {message}")
        try:
            staged.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            if backup.exists():
                restore_backup(target, backup, log_file)
            if target.exists():
                try:
                    launch_target(target)
                except Exception as relaunch_exc:
                    append_log(log_file, f"Could not relaunch working app: {relaunch_exc}")
        except Exception as rollback_exc:
            append_log(log_file, f"Rollback failed: {rollback_exc}")

        write_result(result_file, "failed", version, message)
        return 4


def install_with_setup(
    target: Path,
    source: Path,
    pid: int,
    version: str,
    result_file: Path,
    log_file: Path,
) -> int:
    append_log(log_file, f"Installer updater started for version {version}. Target={target}")

    if not source.exists():
        message = "Downloaded installer file is missing."
        append_log(log_file, message)
        write_result(result_file, "failed", version, message)
        return 5

    if not wait_for_process_exit(pid, timeout_seconds=45):
        message = "The application did not close in time."
        append_log(log_file, message)
        write_result(result_file, "failed", version, message)
        return 6

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    install_dir = target.parent

    try:
        result_code = None
        for attempt in (1, 2):
            # Helper programs started by the app (mpv, ffmpeg) can outlive it and lock files.
            if stop_processes_in_folder(install_dir, log_file):
                time.sleep(1.5)
            setup_log = log_file.with_name(f"setup-{version}-{attempt}.log")
            command = [
                str(source),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
                f"/LOG={setup_log}",
            ]
            append_log(log_file, f"Launching installer (attempt {attempt}): {' '.join(command)}")
            result = subprocess.run(
                command,
                cwd=str(source.parent),
                timeout=600,
                check=False,
                creationflags=creationflags,
            )
            result_code = result.returncode
            if result_code == 0:
                break
            details = setup_log_errors(setup_log)
            append_log(log_file, f"Installer exited with code {result_code}. {INSTALLER_EXIT_CODES.get(result_code, '')}"
                                 + (f"\n{details}" if details else ""))
            if result_code not in {3, 4, 5, 7} or attempt == 2:
                break
            time.sleep(3)
        if result_code != 0:
            reason = INSTALLER_EXIT_CODES.get(result_code, "")
            raise RuntimeError(f"Installer exited with code {result_code}. {reason}".strip())

        if not target.exists():
            raise RuntimeError("Installer completed, but the application executable is missing.")

        source.unlink(missing_ok=True)
        write_result(result_file, "success", version, "Installer update completed successfully.")
        append_log(log_file, f"Installer update {version} completed successfully.")
        launch_target(target)
        return 0
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        append_log(log_file, f"Installer update failed: {message}")
        write_result(result_file, "failed", version, message)
        try:
            if target.exists():
                launch_target(target)
        except Exception as relaunch_exc:
            append_log(log_file, f"Could not relaunch app after installer failure: {relaunch_exc}")
        return 7


def main() -> int:
    parser = argparse.ArgumentParser(description="Team Fahad Downloader updater")
    parser.add_argument("--target", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--version", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--installer", action="store_true")
    args = parser.parse_args()

    kwargs = {
        "target": Path(args.target).resolve(),
        "source": Path(args.source).resolve(),
        "pid": args.pid,
        "version": args.version,
        "result_file": Path(args.result_file).resolve(),
        "log_file": Path(args.log_file).resolve(),
    }
    if args.installer:
        return install_with_setup(**kwargs)
    return install_update(**kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
