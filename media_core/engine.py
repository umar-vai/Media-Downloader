from __future__ import annotations

import copy
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe

from .browser_resolver import resolve_with_installed_browser
from .sources import detect_platform, extraction_attempts, platform_name, video_format_selector


StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]
MetricsCallback = Callable[[dict[str, Any]], None]


class Cancelled(RuntimeError):
    pass


def _check_cancel(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise Cancelled("Operation cancelled.")


def safe_filename(value: str, fallback: str = "media_download") -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.(mp3|m4a|mp4|webm|mkv|mov|wav)$", "", text, flags=re.I)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:150] or fallback).strip()


def _public_summary(url: str, info: dict[str, Any]) -> dict[str, Any]:
    formats = [item for item in (info.get("formats") or []) if isinstance(item, dict)]
    heights = sorted(
        {
            int(item.get("height") or 0)
            for item in formats
            if str(item.get("vcodec") or "") != "none" and int(item.get("height") or 0) > 0
        }
    )
    qualities = [f"{value}p" for value in heights]
    if "Best available" not in qualities:
        qualities.append("Best available")

    return {
        "url": url,
        "title": str(info.get("title") or info.get("description") or "Media"),
        "creator": str(info.get("channel") or info.get("uploader") or info.get("uploader_id") or ""),
        "duration": float(info.get("duration") or 0),
        "thumbnail": str(info.get("thumbnail") or ""),
        "platform": platform_name(url),
        "qualities": qualities,
        "format_count": len(formats),
        "filename": safe_filename(str(info.get("title") or "media_download")),
    }


def analyze_url(
    url: str,
    *,
    cancel_event: threading.Event,
    on_status: StatusCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not detect_platform(url):
        raise ValueError("Please provide a valid http:// or https:// media URL.")

    attempts = extraction_attempts(url)
    errors: list[str] = []
    info: dict[str, Any] = {}

    for index, (attempt_url, network_options) in enumerate(attempts, start=1):
        _check_cancel(cancel_event)
        if on_status:
            on_status(f"Connection {index}/{len(attempts)}")

        options = dict(network_options or {})
        force_generic = bool(options.pop("_force_generic_extractor", False))
        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "cachedir": False,
                    "socket_timeout": 12,
                    "retries": 0,
                    "fragment_retries": 0,
                    **options,
                }
            ) as ydl:
                info = ydl.extract_info(
                    attempt_url,
                    download=False,
                    force_generic_extractor=force_generic,
                ) or {}
            if info:
                break
        except Exception as exc:
            errors.append(f"Attempt {index}: {str(exc).strip()}")

    if not info:
        _check_cancel(cancel_event)
        if on_status:
            on_status("Trying installed-browser fallback")
        try:
            info = resolve_with_installed_browser(
                url,
                cancel_event=cancel_event,
                capture_seconds=12.0,
            )
        except Exception as exc:
            errors.append(f"Installed browser: {str(exc).strip()}")

    _check_cancel(cancel_event)
    if not info:
        detail = "\n".join(errors[-5:])
        raise RuntimeError(
            "No compatible extraction path succeeded."
            + (f"\n{detail}" if detail else "")
        )

    return _public_summary(url, info), info


def _available_name(directory: Path, requested: str) -> str:
    base = safe_filename(requested)
    candidate = base
    index = 2
    while any(path.is_file() and path.stem == candidate for path in directory.glob(f"{candidate}.*")):
        candidate = f"{base} ({index})"
        index += 1
    return candidate


def download_from_analysis(
    *,
    url: str,
    cached_info: dict[str, Any],
    download_dir: Path,
    filename: str,
    mode: str,
    video_quality: str,
    audio_format: str,
    audio_quality: str,
    cancel_event: threading.Event,
    on_status: StatusCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_metrics: MetricsCallback | None = None,
) -> Path:
    download_dir = Path(download_dir).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)
    name = _available_name(download_dir, filename)
    started_at = time.time()

    def hook(data: dict[str, Any]) -> None:
        _check_cancel(cancel_event)
        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            progress = (downloaded / total) if total else 0.0
            speed = data.get("speed")
            eta = data.get("eta")
            bits: list[str] = []
            if speed:
                bits.append(f"{float(speed) / 1024 / 1024:.1f} MB/s")
            if eta is not None:
                bits.append(f"ETA {int(eta)}s")
            if on_metrics:
                on_metrics(
                    {
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "speed_bps": float(speed or 0.0),
                        "eta_seconds": int(eta) if eta is not None else None,
                    }
                )
            if on_progress:
                on_progress(max(0.0, min(1.0, progress)), " • ".join(bits) or "Downloading")
        elif status == "finished" and on_status:
            on_status("Finalizing file")

    opts: dict[str, Any] = {
        "outtmpl": str(download_dir / f"{name}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 4,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "overwrites": False,
        "continuedl": True,
        "ffmpeg_location": get_ffmpeg_exe(),
    }

    if mode.lower() == "audio":
        opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": str(audio_format or "mp3").lower(),
                        "preferredquality": str(audio_quality or "192"),
                    }
                ],
            }
        )
    else:
        opts["format"] = video_format_selector(url, video_quality or "Best available")
        opts["merge_output_format"] = "mp4"

    errors: list[str] = []
    downloaded_ok = False

    attempts = extraction_attempts(url)

    def cleanup_transient_files() -> None:
        # Keep .part/.ytdl so yt-dlp can resume after a retry or process restart.
        for partial in download_dir.glob(f"{name}.*"):
            if partial.suffix.lower() in {".tmp", ".temp"}:
                try:
                    partial.unlink()
                except OSError:
                    pass

    _check_cancel(cancel_event)
    if cached_info:
        try:
            if on_status:
                on_status("Using analyzed media data")
            cached_network_options: dict[str, Any] = {}
            if attempts:
                cached_network_options = dict(attempts[0][1] or {})
                cached_network_options.pop("_force_generic_extractor", None)
            with yt_dlp.YoutubeDL({**opts, **cached_network_options}) as ydl:
                ydl.process_ie_result(copy.deepcopy(cached_info), download=True)
            downloaded_ok = True
        except Cancelled:
            raise
        except Exception as exc:
            errors.append(f"Analyzed media: {str(exc).strip()}")
            cleanup_transient_files()

    if not downloaded_ok:
        _check_cancel(cancel_event)
        try:
            if on_status:
                on_status("Refreshing media information")
            _summary, refreshed_info = analyze_url(
                url,
                cancel_event=cancel_event,
                on_status=(
                    (lambda text: on_status(f"Refresh: {text}"))
                    if on_status
                    else None
                ),
            )
            refreshed_network_options: dict[str, Any] = {}
            if attempts:
                refreshed_network_options = dict(attempts[0][1] or {})
                refreshed_network_options.pop("_force_generic_extractor", None)
            with yt_dlp.YoutubeDL({**opts, **refreshed_network_options}) as ydl:
                ydl.process_ie_result(copy.deepcopy(refreshed_info), download=True)
            downloaded_ok = True
        except Cancelled:
            raise
        except Exception as exc:
            errors.append(f"Fresh media info: {str(exc).strip()}")
            cleanup_transient_files()

    if not downloaded_ok:
        for index, (attempt_url, network_options) in enumerate(attempts, start=1):
            _check_cancel(cancel_event)
            if on_status:
                on_status(f"URL fallback {index}/{len(attempts)}")
            attempt_options = dict(network_options or {})
            force_generic = bool(attempt_options.pop("_force_generic_extractor", False))
            try:
                with yt_dlp.YoutubeDL({**opts, **attempt_options}) as ydl:
                    ydl.extract_info(
                        attempt_url,
                        download=True,
                        force_generic_extractor=force_generic,
                    )
                downloaded_ok = True
                break
            except Cancelled:
                raise
            except Exception as exc:
                errors.append(f"Fallback {index}: {str(exc).strip()}")
                cleanup_transient_files()

    _check_cancel(cancel_event)
    if not downloaded_ok:
        raise RuntimeError(
            "No compatible download path succeeded."
            + (f"\n" + "\n".join(errors[-5:]) if errors else "")
        )

    candidates: list[Path] = []
    for path in download_dir.glob(f"{name}.*"):
        if not path.is_file() or path.suffix.lower() in {".part", ".ytdl", ".tmp", ".temp"}:
            continue
        try:
            if path.stat().st_mtime >= started_at - 2:
                candidates.append(path)
        except OSError:
            pass

    if not candidates:
        raise RuntimeError("Download finished, but the final file could not be located.")

    output = max(candidates, key=lambda item: item.stat().st_mtime)
    if on_progress:
        on_progress(1.0, "Complete")
    return output
