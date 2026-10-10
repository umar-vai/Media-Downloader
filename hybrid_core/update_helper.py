from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def atomic_replace(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Staged update is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_suffix(target.suffix + ".previous")
    if backup.exists():
        backup.unlink()
    if target.exists():
        shutil.copy2(target, backup)
    temp = target.with_suffix(target.suffix + ".new")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def launch(target: Path) -> None:
    subprocess.Popen(
        [str(target), "--updated"],
        cwd=str(target.parent),
        close_fds=True,
        creationflags=CREATE_NO_WINDOW,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--restart-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        payload = {"ok": True, "helper": "MediaDownloaderCoreUpdater"}
        if args.report:
            Path(args.report).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0

    if not args.target:
        return 2
    target = Path(args.target).resolve()
    try:
        if args.wait_pid:
            wait_for_exit(args.wait_pid)
        if not args.restart_only:
            if not args.source:
                raise ValueError("A staged update source is required.")
            atomic_replace(Path(args.source), target)
        launch(target)
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
