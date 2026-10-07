from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe

from install_mode import install_mode_name
from media_player import _mpv_executable
from version import APP_VERSION

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _run_version_check(name: str, executable: Path | str, args: list[str]) -> CheckResult:
    path = Path(executable)
    if not path.exists():
        return CheckResult(name, False, f"Missing executable: {path}")
    try:
        result = subprocess.run(
            [str(path), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=12,
            check=False,
        )
    except Exception as exc:
        return CheckResult(name, False, f"Could not run {path.name}: {exc}")

    output = (result.stdout or result.stderr or "").strip().splitlines()
    detail = output[0][:240] if output else f"exit={result.returncode}"
    return CheckResult(name, result.returncode == 0, detail)


def _check_writable(name: str, directory: Path) -> CheckResult:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="media-downloader-self-test-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as handle:
            handle.write("ok")
            temp_path = Path(handle.name)
        temp_path.unlink(missing_ok=True)
        return CheckResult(name, True, str(directory))
    except Exception as exc:
        return CheckResult(name, False, f"{directory}: {exc}")


def run_self_test() -> dict[str, Any]:
    checks: list[CheckResult] = []

    version_parts = APP_VERSION.split(".")
    version_ok = len(version_parts) == 3 and all(part.isdigit() for part in version_parts)
    checks.append(CheckResult("app_version", version_ok, APP_VERSION))

    try:
        ffmpeg = Path(get_ffmpeg_exe())
        checks.append(_run_version_check("ffmpeg", ffmpeg, ["-version"]))
    except Exception as exc:
        checks.append(CheckResult("ffmpeg", False, str(exc)))

    try:
        mpv = _mpv_executable()
        checks.append(_run_version_check("mpv", mpv, ["--version"]))
    except Exception as exc:
        checks.append(CheckResult("mpv", False, str(exc)))

    updater = _resource_path("MediaDownloaderUpdater.exe")
    if getattr(sys, "frozen", False):
        checks.append(
            CheckResult(
                "updater",
                updater.exists() and updater.stat().st_size > 0,
                str(updater) if updater.exists() else "Bundled updater is missing.",
            )
        )
    else:
        source_updater = Path(__file__).resolve().with_name("updater.py")
        checks.append(
            CheckResult(
                "updater",
                source_updater.exists(),
                f"source mode: {source_updater}",
            )
        )

    try:
        yt_version = str(getattr(yt_dlp.version, "__version__", "unknown"))
        checks.append(CheckResult("yt_dlp", yt_version != "unknown", yt_version))
    except Exception as exc:
        checks.append(CheckResult("yt_dlp", False, str(exc)))

    config_dir = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
    local_dir = Path(os.getenv("LOCALAPPDATA") or config_dir) / "MediaDownloader"
    download_dir = Path.home() / "Downloads" / "Media Downloader"

    checks.append(_check_writable("config_write", config_dir))
    checks.append(_check_writable("local_data_write", local_dir))
    checks.append(_check_writable("download_write", download_dir))

    all_ok = all(check.ok for check in checks)
    return {
        "ok": all_ok,
        "version": APP_VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "install_mode": install_mode_name(),
        "python": sys.version.split()[0],
        "checks": [asdict(check) for check in checks],
    }


def write_report(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Media Downloader runtime self-test")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    report = run_self_test()
    if args.report:
        write_report(report, args.report)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
