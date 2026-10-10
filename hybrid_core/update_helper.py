from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEALTH_URL = "http://127.0.0.1:38477/api/health"


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_exit(pid: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_running(pid):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for process {pid} to exit.")


def previous_path(target: Path) -> Path:
    target = Path(target)
    return target.with_suffix(target.suffix + ".previous")


def failed_path(target: Path) -> Path:
    target = Path(target)
    return target.with_suffix(target.suffix + ".failed")


def atomic_replace(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Staged update is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = previous_path(target)
    if backup.exists():
        backup.unlink()
    if target.exists():
        shutil.copy2(target, backup)
    temp = target.with_suffix(target.suffix + ".new")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def restore_previous_after_failed_update(target: Path) -> None:
    target = target.resolve()
    backup = previous_path(target)
    if not backup.is_file():
        raise FileNotFoundError("Previous Local Core backup is unavailable.")
    failed = failed_path(target)
    if failed.exists():
        failed.unlink()
    if target.exists():
        shutil.copy2(target, failed)
    temp = target.with_suffix(target.suffix + ".restore")
    shutil.copy2(backup, temp)
    os.replace(temp, target)
    backup.unlink(missing_ok=True)


def swap_with_previous(target: Path) -> None:
    target = target.resolve()
    backup = previous_path(target)
    if not target.is_file():
        raise FileNotFoundError(f"Current Local Core is missing: {target}")
    if not backup.is_file():
        raise FileNotFoundError("Previous Local Core backup is unavailable.")

    current_copy = target.with_suffix(target.suffix + ".rollback-current")
    incoming = target.with_suffix(target.suffix + ".rollback-new")
    current_copy.unlink(missing_ok=True)
    incoming.unlink(missing_ok=True)

    shutil.copy2(target, current_copy)
    shutil.copy2(backup, incoming)
    os.replace(incoming, target)
    shutil.copy2(current_copy, backup)
    current_copy.unlink(missing_ok=True)


def launch(target: Path, *extra_args: str) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        [str(target), *extra_args],
        cwd=str(target.parent),
        close_fds=True,
        creationflags=CREATE_NO_WINDOW,
    )


def terminate_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


def _read_health() -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def wait_for_health(
    process: subprocess.Popen[Any],
    *,
    expected_version: str = "",
    timeout: float = 35.0,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        payload = _read_health()
        if payload and bool(payload.get("ok")):
            if not expected_version or str(payload.get("version") or "") == expected_version:
                return True
        time.sleep(0.25)
    return False


def apply_update_with_rollback(
    source: Path,
    target: Path,
    *,
    expected_version: str = "",
    health_timeout: float = 35.0,
) -> None:
    atomic_replace(source, target)
    process = launch(target, "--updated")
    if wait_for_health(process, expected_version=expected_version, timeout=health_timeout):
        return

    terminate_tree(process)
    restore_previous_after_failed_update(target)
    recovered = launch(target, "--rollback-recovered")
    wait_for_health(recovered, timeout=health_timeout)
    raise RuntimeError("The new Local Core failed its health check and was rolled back automatically.")


def rollback_with_recovery(target: Path, *, health_timeout: float = 35.0) -> None:
    swap_with_previous(target)
    process = launch(target, "--rollback-recovered")
    if wait_for_health(process, timeout=health_timeout):
        return

    terminate_tree(process)
    swap_with_previous(target)
    recovered = launch(target, "--rollback-recovery-failed")
    wait_for_health(recovered, timeout=health_timeout)
    raise RuntimeError("The previous Local Core failed its health check, so the rollback was reversed.")


def install_updated_helper(target: Path | None) -> None:
    if target is None:
        return
    target = target.resolve()
    source = Path(sys.executable).resolve()
    if source == target or not source.is_file():
        return
    temp = target.with_suffix(target.suffix + ".new")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--health-timeout", type=float, default=35.0)
    parser.add_argument("--install-helper-target")
    parser.add_argument("--restart-only", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        payload = {
            "ok": True,
            "helper": "MediaDownloaderCoreUpdater",
            "health_check": True,
            "rollback": True,
        }
        if args.report:
            Path(args.report).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0

    if not args.target:
        return 2

    target = Path(args.target).resolve()
    try:
        if args.wait_pid:
            wait_for_exit(args.wait_pid)

        if args.rollback:
            rollback_with_recovery(target, health_timeout=max(5.0, float(args.health_timeout)))
        elif args.restart_only:
            launch(target, "--updated")
        else:
            if not args.source:
                raise ValueError("A staged update source is required.")
            apply_update_with_rollback(
                Path(args.source),
                target,
                expected_version=str(args.expected_version or ""),
                health_timeout=max(5.0, float(args.health_timeout)),
            )
            if args.install_helper_target:
                try:
                    install_updated_helper(Path(args.install_helper_target))
                except Exception:
                    pass
        return 0
    except Exception as exc:
        log = target.parent / "MediaDownloaderCoreUpdateError.txt"
        try:
            log.write_text(str(exc), encoding="utf-8")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
