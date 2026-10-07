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

    command = [
        str(source),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    try:
        append_log(log_file, f"Launching installer: {' '.join(command)}")
        result = subprocess.run(
            command,
            cwd=str(source.parent),
            timeout=600,
            check=False,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Installer exited with code {result.returncode}.")

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
