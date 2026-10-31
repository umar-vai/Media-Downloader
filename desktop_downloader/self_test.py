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

from install_mode import install_mode_name
from version import APP_VERSION

# Source tests are invoked as "python desktop_downloader/self_test.py", so
# Python adds desktop_downloader/ (not the repository root) to sys.path.
# Frozen builds use PyInstaller's embedded module loader instead.
if not getattr(sys, "frozen", False):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

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


def run_self_test(progress_path: Path | None = None) -> dict[str, Any]:
    checks: list[CheckResult] = []

    def record(check: CheckResult) -> None:
        checks.append(check)
        if progress_path is not None:
            write_report(
                {
                    "stage": "checking",
                    "last_check": check.name,
                    "checks": [asdict(item) for item in checks],
                },
                progress_path,
            )

    version_parts = APP_VERSION.split(".")
    version_ok = len(version_parts) == 3 and all(part.isdigit() for part in version_parts)
    record(CheckResult("app_version", version_ok, APP_VERSION))

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg = Path(get_ffmpeg_exe())
        record(_run_version_check("ffmpeg", ffmpeg, ["-version"]))
    except Exception as exc:
        record(CheckResult("ffmpeg", False, str(exc)))

    try:
        from media_player import _mpv_executable

        mpv = _mpv_executable()
        record(_run_version_check("mpv", mpv, ["--version"]))
    except Exception as exc:
        record(CheckResult("mpv", False, str(exc)))

    updater = _resource_path("MediaDownloaderUpdater.exe")
    if getattr(sys, "frozen", False):
        record(
            CheckResult(
                "updater",
                updater.exists() and updater.stat().st_size > 0,
                str(updater) if updater.exists() else "Bundled updater is missing.",
            )
        )
    else:
        source_updater = Path(__file__).resolve().with_name("updater.py")
        record(
            CheckResult(
                "updater",
                source_updater.exists(),
                f"source mode: {source_updater}",
            )
        )

    try:
        import yt_dlp

        yt_version = str(getattr(yt_dlp.version, "__version__", "unknown"))
        record(CheckResult("yt_dlp", yt_version != "unknown", yt_version))
    except Exception as exc:
        record(CheckResult("yt_dlp", False, str(exc)))

    try:
        # The launcher imports app in normal GUI mode, so this smoke check must
        # import it too. Otherwise "--self-test" can pass while a missing
        # frozen module crashes the real Desktop EXE at startup.
        import media_core.engine  # noqa: F401
        import media_core.sources  # noqa: F401
        import media_core.browser_resolver  # noqa: F401
        import media_core.network  # noqa: F401
        from app import DownloaderApp

        record(
            CheckResult(
                "desktop_startup_imports",
                bool(DownloaderApp),
                "Desktop app and shared media_core modules import successfully.",
            )
        )
    except Exception as exc:
        record(CheckResult("desktop_startup_imports", False, f"{type(exc).__name__}: {exc}"))

    config_dir = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
    local_dir = Path(os.getenv("LOCALAPPDATA") or config_dir) / "MediaDownloader"
    download_dir = Path.home() / "Downloads" / "Media Downloader"

    record(_check_writable("config_write", config_dir))
    record(_check_writable("local_data_write", local_dir))
    record(_check_writable("download_write", download_dir))

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

    progress_path = args.report.with_suffix(".progress.json") if args.report else None
    if progress_path is not None:
        write_report({"stage": "started", "checks": []}, progress_path)

    report = run_self_test(progress_path=progress_path)
    if args.report:
        write_report(report, args.report)
    if progress_path is not None:
        write_report(
            {"stage": "completed", "ok": report["ok"], "checks": report["checks"]},
            progress_path,
        )
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
