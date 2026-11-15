from __future__ import annotations

import copy
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe

from . import options as engine_options
from .browser_resolver import resolve_with_installed_browser
from .sources import (
    browser_session_attempts,
    detect_platform,
    extraction_attempts,
    is_pornhub_url,
    platform_name,
    video_format_selector,
)


StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]
MetricsCallback = Callable[[dict[str, Any]], None]


class Cancelled(RuntimeError):
    pass


def _check_cancel(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise Cancelled("Operation cancelled.")


_PLACEHOLDER_TITLES = {
    "untitled", "untitled video", "no title", "video", "media", "browser media", "media download",
    "download", "watch", "player", "embed", "index", "master", "playlist", "stream", "videoplayback",
}


def is_placeholder_title(value: Any) -> bool:
    """A name that says nothing about the video ("Untitled", "Video", "master"...).

    Different videos get the same placeholder when a site hides its title, so such a name
    must never be taken as proof that the video is already downloaded."""
    text = re.sub(r"[\s._-]+", " ", str(value or "")).strip().lower()
    text = re.sub(r"\s*\(\d+\)$", "", text)
    return not text or text in _PLACEHOLDER_TITLES


# yt-dlp adds "-movflags +faststart" to every ffmpeg output. That moves the index to the front
# by rewriting the whole file a second time, which doubles the time a large video spends in
# "Merging" (players on this PC do not need it). A later -movflags wins, so turn it off.
_NO_FASTSTART = ["-movflags", "-faststart"]


class _StreamPlanPP(yt_dlp.postprocessor.PostProcessor):
    """Runs right before the download starts, once yt-dlp has chosen the formats, and tells
    the progress code how many streams (video + audio) will arrive and how big each is."""

    def __init__(self, on_plan: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(None)
        self._on_plan = on_plan

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        try:
            self._on_plan(info)
        except Cancelled:
            raise
        except Exception:
            pass
        return [], info


class _MetadataPP(yt_dlp.postprocessor.FFmpegMetadataPP):
    """Embeds metadata, but skips the extra full-file rewrite for a merged video: the merge
    step already wrote the same tags (see ``_merge_metadata_args``)."""

    merge_tagged = False

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        if self.merge_tagged and info.get("requested_formats") and not info.get("chapters"):
            return [], info
        return super().run(info)


def _merge_metadata_args(info: dict[str, Any], fallback_title: str) -> list[str]:
    title = str(info.get("title") or "")
    if is_placeholder_title(title):
        title = fallback_title
    values = {
        "title": title,
        "artist": info.get("artist") or info.get("creator") or info.get("uploader") or info.get("channel"),
        "date": info.get("upload_date"),
        "comment": info.get("webpage_url"),
        "purl": info.get("webpage_url"),
    }
    args: list[str] = []
    for key, value in values.items():
        text = str(value or "").replace("\0", "").strip()
        if text:
            args += ["-metadata", f"{key}={text[:500]}"]
    return args


def safe_filename(value: str, fallback: str = "media_download") -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.(mp3|m4a|mp4|webm|mkv|mov|wav|flac|opus|ogg)$", "", text, flags=re.I)
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


def extraction_failure_hint(errors: list[str]) -> str:
    """Explain common extraction failures without exposing raw URLs or session cookies."""
    joined = " ".join(errors).lower()
    if any(word in joined for word in ("widevine", "drm protected", "drm-protected", "encrypted media")):
        return "This media appears DRM-protected; protected streams cannot be downloaded."
    if any(word in joined for word in ("429", "too many requests", "rate limit", "rate-limit")):
        return "The website is rate-limiting requests. Reduce simultaneous downloads and retry later."
    if any(word in joined for word in ("403", "401", "sign in", "login required", "cookies", "unauthorized", "forbidden")):
        return "The website may require an authorized browser session or is denying access."
    if any(word in joined for word in ("404", "410 gone", "video unavailable", "video has been removed")):
        return "The original video URL may be expired, removed, or unavailable."
    if any(word in joined for word in ("timed out", "timeout", "ssl", "tls", "connection reset", "connection aborted")):
        return "A connection or TLS failure prevented extraction. Check connectivity and retry."
    if any(word in joined for word in ("unsupported url", "no video formats", "no playable", "did not observe")):
        return "The player did not expose a supported media stream. Try a direct, public media URL."
    return "No supported media stream was detected. The page may require an interactive player."


_REPORT_HEADERS = {"referer": "Referer", "origin": "Origin", "user-agent": "User-Agent", "cookie": "Cookie"}


def info_from_browser_report(page_url: str, report: dict[str, Any]) -> dict[str, Any]:
    """Build an info dict from the streams the browser extension saw the page's player load."""
    streams: list[dict[str, Any]] = []
    formats: list[dict[str, Any]] = []
    for raw in list(report.get("streams") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        stream_url = str(raw.get("url") or "")
        if not stream_url.startswith(("http://", "https://")) or raw.get("drm"):
            continue
        headers: dict[str, str] = {}
        for key, value in dict(raw.get("headers") or {}).items():
            name = _REPORT_HEADERS.get(str(key).strip().lower())
            text = str(value or "").strip()
            if name and text and len(text) <= 8192 and not any(ch in text for ch in "\r\n"):
                headers[name] = text
        referer = str(raw.get("referer") or "")
        if referer.startswith(("http://", "https://")):
            headers.setdefault("Referer", referer)
        kind = str(raw.get("kind") or "file")
        streams.append({"url": stream_url, "kind": kind, "master": bool(raw.get("master")), "http_headers": headers})
        lower = stream_url.lower().split("?", 1)[0]
        ext = lower.rsplit(".", 1)[-1] if "." in lower.rsplit("/", 1)[-1] else "mp4"
        formats.append(
            {
                "format_id": f"browser-{len(formats) + 1}",
                "url": stream_url,
                "ext": "mp4" if kind in {"hls", "dash"} else (ext if 1 <= len(ext) <= 5 else "mp4"),
                "protocol": {"hls": "m3u8_native", "dash": "http_dash_segments"}.get(kind, "https"),
                "http_headers": headers,
            }
        )
    title = " ".join(str(report.get("title") or "").split())[:180] or "Browser media"
    return {
        "id": f"browser-{abs(hash(page_url))}",
        "display_id": f"browser-{abs(hash(page_url))}",
        "title": title,
        "webpage_url": page_url,
        "original_url": page_url,
        "extractor": "BrowserExtension",
        "extractor_key": "BrowserExtension",
        "formats": formats,
        "_browser_streams": streams,
    }


def expand_browser_capture(
    page_url: str,
    info: dict[str, Any],
    *,
    cancel_event: threading.Event,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Turn a stream captured by the hidden browser into real formats.

    A captured HLS master playlist is read with yt-dlp (using the exact Referer/Origin/cookies
    the player used), which lists every quality (1080p, 720p, ...) and the separate audio
    track, so the chosen quality is downloaded with its sound."""
    streams = [item for item in (info.get("_browser_streams") or []) if isinstance(item, dict)]
    for stream in streams:
        if stream.get("kind") not in {"hls", "dash"} or stream.get("drm"):
            continue
        _check_cancel(cancel_event)
        if on_status:
            on_status("Reading stream qualities")
        headers = {str(k): str(v) for k, v in dict(stream.get("http_headers") or {}).items() if v}
        try:
            with yt_dlp.YoutubeDL(
                engine_options.apply_request_options({
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "cachedir": engine_options.cache_dir(),
                    "socket_timeout": 15,
                    "retries": 1,
                    "http_headers": headers,
                })
            ) as ydl:
                expanded = ydl.extract_info(str(stream.get("url")), download=False, force_generic_extractor=True) or {}
        except Exception:
            continue
        if not expanded.get("formats"):
            continue
        expanded["title"] = str(info.get("title") or expanded.get("title") or "Browser media")
        expanded["webpage_url"] = page_url
        expanded["original_url"] = page_url
        return expanded
    return info


def analyze_url(
    url: str,
    *,
    cancel_event: threading.Event,
    on_status: StatusCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not detect_platform(url):
        raise ValueError("Please provide a valid http:// or https:// media URL.")

    attempts = extraction_attempts(url)
    quick = engine_options.quick_analysis()
    if quick:
        # The extension is waiting on a menu: the first transports are the ones that work.
        attempts = attempts[:3]
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
                engine_options.apply_request_options({
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "cachedir": engine_options.cache_dir(),
                    "socket_timeout": 12,
                    "retries": 0,
                    "fragment_retries": 0,
                    **options,
                })
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

    if not info and not quick:
        session_attempts = browser_session_attempts(url)
        for index, (attempt_url, session_options) in enumerate(session_attempts, start=1):
            _check_cancel(cancel_event)
            if on_status:
                on_status(f"Trying browser session {index}/{len(session_attempts)}")
            options = dict(session_options or {})
            force_generic = bool(options.pop("_force_generic_extractor", False))
            try:
                with yt_dlp.YoutubeDL(
                    engine_options.apply_request_options({
                        "quiet": True,
                        "no_warnings": True,
                        "skip_download": True,
                        "noplaylist": True,
                        "cachedir": engine_options.cache_dir(),
                        "socket_timeout": 18,
                        "retries": 1,
                        "fragment_retries": 1,
                        **options,
                    })
                ) as ydl:
                    info = ydl.extract_info(
                        attempt_url,
                        download=False,
                        force_generic_extractor=force_generic,
                    ) or {}
                if info:
                    break
            except Exception as exc:
                errors.append(f"Browser session {index}: {str(exc).strip()}")

    if not info:
        bridge = engine_options.browser_bridge()
        if callable(bridge):
            _check_cancel(cancel_event)
            if on_status:
                on_status("Opening the page in your browser to find the stream")
            try:
                report = bridge(url, cancel_event)
                if report and report.get("streams"):
                    info = info_from_browser_report(url, report)
                    info = expand_browser_capture(url, info, cancel_event=cancel_event, on_status=on_status)
                elif report is not None:
                    errors.append("Your browser: the player did not load a playable stream.")
            except Cancelled:
                raise
            except Exception as exc:
                errors.append(f"Your browser: {str(exc).strip()}")

    if not info and not quick:
        _check_cancel(cancel_event)
        if on_status:
            on_status("Trying installed-browser fallback")
        try:
            info = resolve_with_installed_browser(
                url,
                cancel_event=cancel_event,
                capture_seconds=25.0,
            )
            info = expand_browser_capture(url, info, cancel_event=cancel_event, on_status=on_status)
        except Cancelled:
            raise
        except Exception as exc:
            errors.append(f"Installed browser: {str(exc).strip()}")

    _check_cancel(cancel_event)
    if not info:
        if is_pornhub_url(url):
            raise RuntimeError(
                "Could not find a playable stream from this page. "
                + extraction_failure_hint(errors) + " "
                "Tried the site extractor, legacy HTTPS transport, installed-browser session cookies, "
                "and browser media capture. If the video plays in your normal browser, keep that browser "
                "profile available and Analyze again. DRM-protected streams are not supported."
            )
        raise RuntimeError(
            "Could not find a playable stream from this page. "
            + extraction_failure_hint(errors) + " "
            "Tried legacy HTTPS, the site/Generic extractor, installed-browser session cookies, "
            "and browser media capture. If the page plays only after selecting an episode/server or "
            "passing a browser challenge, open it once in Chrome/Edge and Analyze again. "
            "DRM-protected streams are not supported."
        )

    return _public_summary(url, info), info


def _files_with_stem(directory: Path, stem: str) -> list[Path]:
    """Files named ``<stem>.<ext>``. Compares names directly instead of using glob,
    because titles such as ``Song [Official] *Live*`` contain glob metacharacters
    and would never match (breaking duplicate detection and final-file lookup)."""
    found: list[Path] = []
    try:
        for path in directory.iterdir():
            if path.is_file() and path.stem == stem:
                found.append(path)
    except OSError:
        pass
    return found


def _available_name(directory: Path, requested: str) -> str:
    base = safe_filename(requested)
    candidate = base
    index = 2
    while [p for p in _files_with_stem(directory, candidate) if engine_options.is_final_media(p)]:
        candidate = f"{base} ({index})"
        index += 1
    return candidate


def _format_size(value: float) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _format_eta(seconds: Any) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


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
    # "Skip files that already exist" compares names only, so it is safe only for a real
    # title: many different videos come back as "Untitled" and must each be downloaded.
    if engine_options.skip_existing() and not is_placeholder_title(filename):
        wanted_audio = mode.lower() == "audio"
        expected_size = 0
        if isinstance(cached_info, dict):
            expected_size = int(cached_info.get("filesize") or cached_info.get("filesize_approx") or 0)
        audio_suffixes = {".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg", ".wav"}
        for existing in _files_with_stem(download_dir, safe_filename(filename)):
            try:
                usable = (
                    engine_options.is_final_media(existing)
                    and existing.stat().st_size > 0
                    and (existing.suffix.lower() in audio_suffixes) == wanted_audio
                )
                if usable and expected_size and not wanted_audio:
                    # Same title, clearly a different video (sizes far apart): download it too.
                    ratio = existing.stat().st_size / expected_size
                    usable = 0.8 <= ratio <= 1.25
            except OSError:
                usable = False
            if usable:
                if on_status:
                    on_status("Already downloaded — skipped")
                if on_progress:
                    on_progress(1.0, "Already downloaded")
                return existing
    name = _available_name(download_dir, filename)
    started_at = time.time()

    # Video+audio are downloaded as separate streams and then merged. A naive hook
    # reports each stream from 0-100% so the bar runs forward, snaps back and runs
    # again. Track every stream, weight it by its size and only ever move forward.
    stream_state: dict[str, dict[str, float]] = {}
    expected_streams = 1
    weights: dict[str, float] = {}
    if isinstance(cached_info, dict):
        requested = cached_info.get("requested_formats") or []
        if isinstance(requested, list) and len(requested) > 1:
            expected_streams = len(requested)
            for item in requested:
                if isinstance(item, dict):
                    weights[str(item.get("format_id") or "")] = float(
                        item.get("filesize") or item.get("filesize_approx") or 0
                    )
    best_progress = 0.0

    def on_plan(info: dict[str, Any]) -> None:
        # yt-dlp chose the formats: a video+audio download has two streams. Without this the
        # first finished stream looked like the whole file and the bar sat at 98.5%.
        nonlocal expected_streams
        requested = info.get("requested_formats") or []
        if isinstance(requested, list) and len(requested) > 1:
            expected_streams = len(requested)
            weights.clear()
            for item in requested:
                if isinstance(item, dict):
                    weights[str(item.get("format_id") or "")] = float(
                        item.get("filesize") or item.get("filesize_approx") or 0
                    )

    plan_pp = _StreamPlanPP(on_plan)

    def overall_progress() -> float:
        nonlocal best_progress
        streams = list(stream_state.items())
        count = max(expected_streams, len(streams))
        weight_total = sum(weights.values()) if weights and all(weights.values()) else 0.0
        value = 0.0
        for key, state in streams:
            fraction = state["done"] if state.get("finished") else state["fraction"]
            weight = (weights.get(key, 0.0) / weight_total) if weight_total else 1.0 / count
            value += fraction * weight
        # leave headroom for the merge / conversion step
        value = min(value, 0.985)
        best_progress = max(best_progress, value)
        return best_progress

    def hook(data: dict[str, Any]) -> None:
        _check_cancel(cancel_event)
        status = data.get("status")
        info_dict = data.get("info_dict") or {}
        key = str(info_dict.get("format_id") or data.get("filename") or "stream")
        state = stream_state.setdefault(key, {"fraction": 0.0, "done": 1.0, "finished": 0.0})
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            if total:
                fraction = downloaded / total
            elif data.get("fragment_count"):
                fraction = float(data.get("fragment_index") or 0) / float(data["fragment_count"])
            else:
                fraction = state["fraction"]
            state["fraction"] = max(state["fraction"], max(0.0, min(1.0, fraction)))
            speed = data.get("speed")
            eta = data.get("eta")
            bits: list[str] = []
            if speed:
                bits.append(f"{_format_size(float(speed))}/s")
            if total and downloaded:
                bits.append(f"{_format_size(downloaded)} of {_format_size(total)}")
            elif downloaded:
                bits.append(_format_size(downloaded))
            if eta is not None:
                bits.append(f"{_format_eta(eta)} left")
            state["downloaded"] = float(downloaded)
            state["total"] = float(total)
            if on_metrics:
                # Video and audio arrive as separate streams: report the whole download, so
                # "downloaded of size" and the time left describe the file the user gets.
                done_all = sum(s["total"] if s.get("finished") and s.get("total") else s.get("downloaded", 0.0)
                               for s in stream_state.values())
                known_all = sum(s.get("total", 0.0) for s in stream_state.values())
                weight_all = sum(weights.values()) if weights and all(weights.values()) else 0.0
                total_all = max(known_all, weight_all)
                remaining = total_all - done_all
                if speed and total_all and remaining > 0:
                    eta_all: int | None = int(remaining / float(speed))
                else:
                    eta_all = int(eta) if eta is not None else None
                on_metrics(
                    {
                        "downloaded_bytes": int(done_all),
                        "total_bytes": int(total_all),
                        "speed_bps": float(speed or 0.0),
                        "eta_seconds": eta_all,
                    }
                )
            if on_progress:
                if len(stream_state) > 1 or expected_streams > 1:
                    index = list(stream_state).index(key) + 1
                    bits.insert(0, f"Part {index}/{max(expected_streams, len(stream_state))}")
                on_progress(overall_progress(), " • ".join(bits) or "Downloading")
        elif status == "finished":
            state["fraction"] = 1.0
            state["finished"] = 1.0
            finished_bytes = float(data.get("total_bytes") or data.get("downloaded_bytes") or 0)
            if finished_bytes:
                state["total"] = state["downloaded"] = finished_bytes
            if on_progress:
                on_progress(overall_progress(), "Download finished")
            if on_status and len(stream_state) >= expected_streams:
                on_status("Finalizing file")

    def post_hook(data: dict[str, Any]) -> None:
        _check_cancel(cancel_event)
        if data.get("status") == "started" and on_status:
            names = {
                "Merger": "Merging video and audio",
                "FFmpegMerger": "Merging video and audio",
                "ExtractAudio": "Converting audio",
                "FFmpegExtractAudio": "Converting audio",
                "MoveFiles": "Saving file",
            }
            on_status(names.get(str(data.get("postprocessor") or ""), "Processing file"))

    opts: dict[str, Any] = {
        "outtmpl": str(download_dir / f"{name}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "cachedir": engine_options.cache_dir(),
        "socket_timeout": 30,
        # Reliability: patient retries with exponential back-off, resilient to flaky Wi-Fi and
        # antivirus briefly locking the .part file on Windows.
        "retries": 10,
        "fragment_retries": 15,
        "extractor_retries": 3,
        "file_access_retries": 6,
        "retry_sleep_functions": {
            "http": lambda n: min(2 ** n, 20),
            "fragment": lambda n: min(1 + n, 10),
            "extractor": lambda n: min(2 ** n, 15),
            "file_access": lambda n: 0.5 * (n + 1),
        },
        # Speed: more parallel fragments (HLS/DASH) and a larger read buffer.
        "concurrent_fragment_downloads": 8,
        "buffersize": 1 << 18,
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "overwrites": False,
        "continuedl": True,
        "ffmpeg_location": get_ffmpeg_exe(),
        "postprocessor_args": {"ffmpeg": list(_NO_FASTSTART)},
    }

    extra_opts, extra_processors = engine_options.download_extras(mode)
    opts.update(extra_opts)
    # Metadata: for a merged video the tags are written by the merge itself, which saves a
    # whole extra copy of the file. _MetadataPP still tags single files and chapter lists.
    metadata_pp: dict[str, Any] | None = None
    for processor in list(extra_processors):
        if processor.get("key") == "FFmpegMetadata":
            metadata_pp = processor
            extra_processors.remove(processor)
    if metadata_pp is not None and mode.lower() != "audio" and isinstance(cached_info, dict):
        tags = _merge_metadata_args(cached_info, str(filename or ""))
        if tags:
            opts["postprocessor_args"]["merger+ffmpeg_o"] = [*tags, *_NO_FASTSTART]

    def make_ydl(params: dict[str, Any]) -> yt_dlp.YoutubeDL:
        ydl = yt_dlp.YoutubeDL(params)
        ydl.add_postprocessor(plan_pp, when="before_dl")
        if metadata_pp is not None:
            pp = _MetadataPP(
                ydl,
                add_metadata=bool(metadata_pp.get("add_metadata", True)),
                add_chapters=bool(metadata_pp.get("add_chapters", True)),
            )
            pp.merge_tagged = "merger+ffmpeg_o" in opts["postprocessor_args"]
            # First, as before: metadata is written before thumbnails/subtitles are embedded.
            try:
                ydl._pps["post_process"].insert(0, pp)
            except Exception:
                ydl.add_postprocessor(pp)
        return ydl
    if mode.lower() == "audio":
        codec = engine_options.AUDIO_CODECS.get(str(audio_format or "mp3").lower(), "mp3")
        opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": codec,
                        "preferredquality": str(audio_quality or "192"),
                    },
                    *extra_processors,
                ],
            }
        )
    else:
        opts["format"] = video_format_selector(url, video_quality or "Best available", cached_info)
        opts["merge_output_format"] = "mp4"
        if extra_processors:
            opts["postprocessors"] = list(extra_processors)

    errors: list[str] = []
    downloaded_ok = False

    attempts = extraction_attempts(url)

    def cleanup_transient_files() -> None:
        # Keep .part/.ytdl so yt-dlp can resume after a retry or process restart.
        for partial in _files_with_stem(download_dir, name):
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
            with make_ydl(engine_options.apply_request_options({**opts, **cached_network_options})) as ydl:
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
            if mode.lower() != "audio":
                opts["format"] = video_format_selector(url, video_quality or "Best available", refreshed_info)
            refreshed_network_options: dict[str, Any] = {}
            if attempts:
                refreshed_network_options = dict(attempts[0][1] or {})
                refreshed_network_options.pop("_force_generic_extractor", None)
            with make_ydl(engine_options.apply_request_options({**opts, **refreshed_network_options})) as ydl:
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
                with make_ydl(engine_options.apply_request_options({**opts, **attempt_options})) as ydl:
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
    for path in _files_with_stem(download_dir, name):
        if not engine_options.is_final_media(path):
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
