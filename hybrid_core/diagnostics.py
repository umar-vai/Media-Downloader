from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SENSITIVE_KEYS = {"url", "source_url", "source_path", "path", "download_dir", "output_dir"}


def _safe_value(key: str, value: Any) -> Any:
    if key in {"url", "source_url"} and isinstance(value, str):
        try:
            parsed = urlparse(value)
            return f"{parsed.scheme}://{parsed.netloc}/…" if parsed.netloc else "[redacted]"
        except Exception:
            return "[redacted]"
    if key in {"source_path", "path", "download_dir", "output_dir"} and value:
        return f"…/{Path(str(value)).name}"
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(_safe_value(str(key), item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def create_diagnostics_bundle(
    *,
    output_dir: Path,
    log_file: Path,
    settings: dict[str, Any],
    jobs: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    app_data_dir: Path,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = output_dir / f"MediaDownloaderDiagnostics-{stamp}.zip"

    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "platform": platform.platform(),
        "diagnostics": sanitize(diagnostics),
        "settings": sanitize(settings),
        "jobs": sanitize(jobs[-80:]),
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(summary, ensure_ascii=False, indent=2))

        for candidate in sorted(log_file.parent.glob(f"{log_file.name}*")):
            if candidate.is_file():
                archive.write(candidate, arcname=f"logs/{candidate.name}")

        for name in ("MediaDownloaderCoreServerError.txt", "MediaDownloaderCoreUpdateError.txt"):
            candidate = app_data_dir / name
            if candidate.is_file():
                archive.write(candidate, arcname=f"errors/{candidate.name}")

    return target
