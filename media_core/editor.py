from __future__ import annotations

import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from imageio_ffmpeg import get_ffmpeg_exe


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ProgressCallback = Callable[[float, str], None]
StatusCallback = Callable[[str], None]


class EditorCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0


CROP_PRESETS: dict[str, tuple[int, int] | None | str] = {
    "Original": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "Custom": "custom",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}


def ffmpeg_exe() -> str:
    return get_ffmpeg_exe()


def public_media_info(info: MediaInfo) -> dict[str, Any]:
    return asdict(info)


def safe_export_name(value: str, fallback: str = "edited_media") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:160] or fallback).strip()


def probe_media(path: Path) -> MediaInfo:
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("Media file does not exist.")

    command = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(source)]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=20,
    )
    text = result.stderr or ""
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = 0.0
    if duration_match:
        duration = (
            int(duration_match.group(1)) * 3600
            + int(duration_match.group(2)) * 60
            + float(duration_match.group(3))
        )

    video_line = next(
        (
            line
            for line in text.splitlines()
            if " Video: " in line and "attached pic" not in line.lower()
        ),
        "",
    )
    audio_line = next((line for line in text.splitlines() if " Audio: " in line), "")
    size_match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)

    if duration <= 0:
        raise RuntimeError("Could not read the media duration.")

    return MediaInfo(
        duration=duration,
        has_video=bool(video_line),
        has_audio=bool(audio_line),
        width=int(size_match.group(1)) if size_match else 0,
        height=int(size_match.group(2)) if size_match else 0,
        fps=float(fps_match.group(1)) if fps_match else 0.0,
    )


def even_size(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def compute_crop(
    info: MediaInfo,
    preset: str,
    custom: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int] | None:
    if not info.has_video or preset == "Original":
        return None
    if preset not in CROP_PRESETS:
        raise ValueError("Unsupported crop preset.")
    if info.width <= 0 or info.height <= 0:
        raise ValueError("Video dimensions could not be detected.")

    if preset == "Custom":
        if custom is None:
            raise ValueError("Custom crop requires X, Y, width and height.")
        x, y, width, height = (int(value) for value in custom)
        x = max(0, x)
        y = max(0, y)
        width = even_size(width)
        height = even_size(height)
        if x >= info.width or y >= info.height:
            raise ValueError("Custom crop starts outside the source frame.")
        if x + width > info.width or y + height > info.height:
            raise ValueError("Custom crop is outside the source frame.")
        return x, y, width, height

    ratio = CROP_PRESETS[preset]
    if not isinstance(ratio, tuple):
        return None

    target_ratio = ratio[0] / ratio[1]
    source_ratio = info.width / info.height
    if source_ratio > target_ratio:
        height = even_size(info.height)
        width = even_size(int(height * target_ratio))
    else:
        width = even_size(info.width)
        height = even_size(int(width / target_ratio))

    x = max(0, (info.width - width) // 2)
    y = max(0, (info.height - height) // 2)
    return x, y, width, height


def build_video_filters(
    info: MediaInfo,
    crop_preset: str,
    rotate: str,
    speed: float,
    *,
    custom_crop: tuple[int, int, int, int] | None = None,
    include_speed: bool = True,
    preview_size: tuple[int, int] | None = None,
) -> list[str]:
    filters: list[str] = []
    crop = compute_crop(info, crop_preset, custom_crop)
    if crop:
        x, y, width, height = crop
        filters.append(f"crop={width}:{height}:{x}:{y}")

    if rotate == "90°":
        filters.append("transpose=1")
    elif rotate == "180°":
        filters.extend(["hflip", "vflip"])
    elif rotate == "270°":
        filters.append("transpose=2")
    elif rotate != "0°":
        raise ValueError("Unsupported rotation.")

    speed = max(0.5, min(2.0, float(speed)))
    if include_speed and abs(speed - 1.0) > 0.0001:
        filters.append(f"setpts=PTS/{speed:g}")

    if preview_size:
        width, height = preview_size
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return filters


AUDIO_PRESETS = {"Flat", "Voice Clarity", "Bass Boost", "Podcast", "Music", "Normalize"}


def build_audio_filters(
    speed: float,
    volume_percent: float,
    fade_in: float,
    fade_out: float,
    output_duration: float,
    *,
    audio_preset: str = "Flat",
    noise_reduction: bool = False,
) -> list[str]:
    filters: list[str] = []
    speed = max(0.5, min(2.0, float(speed)))
    if abs(speed - 1.0) > 0.0001:
        filters.append(f"atempo={speed:g}")

    if noise_reduction:
        filters.append("afftdn=nf=-25")

    preset = str(audio_preset or "Flat")
    if preset not in AUDIO_PRESETS:
        preset = "Flat"
    if preset == "Voice Clarity":
        filters.extend([
            "highpass=f=80",
            "equalizer=f=3000:t=q:w=1:g=3",
            "equalizer=f=6000:t=q:w=1:g=2",
        ])
    elif preset == "Bass Boost":
        filters.extend([
            "equalizer=f=100:t=q:w=1:g=5",
            "equalizer=f=220:t=q:w=1:g=3",
        ])
    elif preset == "Podcast":
        filters.extend([
            "highpass=f=70",
            "acompressor=threshold=-18dB:ratio=3:attack=20:release=250:makeup=2",
        ])
    elif preset == "Music":
        filters.extend([
            "bass=g=2:f=110:w=0.6",
            "treble=g=2:f=6000:w=0.6",
        ])
    elif preset == "Normalize":
        filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    volume = max(0.0, min(200.0, float(volume_percent))) / 100.0
    if abs(volume - 1.0) > 0.0001:
        filters.append(f"volume={volume:.3f}")

    fade_in = max(0.0, float(fade_in))
    fade_out = max(0.0, float(fade_out))
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={min(fade_in, output_duration):.3f}")
    if fade_out > 0:
        start = max(0.0, output_duration - fade_out)
        filters.append(f"afade=t=out:st={start:.3f}:d={min(fade_out, output_duration):.3f}")
    return filters


def extract_preview_frame(
    path: Path,
    position: float,
    *,
    crop_preset: str = "Original",
    rotate: str = "0°",
    custom_crop: tuple[int, int, int, int] | None = None,
    timeout: float = 12.0,
) -> bytes:
    source = Path(path).expanduser()
    info = probe_media(source)
    if not info.has_video:
        raise ValueError("This media file does not contain video.")

    filters = build_video_filters(
        info,
        crop_preset,
        rotate,
        1.0,
        custom_crop=custom_crop,
        include_speed=False,
        preview_size=(800, 450),
    )
    command = [
        ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, min(float(position), info.duration)):.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-threads",
        "1",
        "-vf",
        ",".join(filters),
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "4",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(error or "Could not render preview frame.")
    return result.stdout


def extract_waveform(path: Path, *, width: int = 900, height: int = 220, timeout: float = 18.0) -> bytes:
    source = Path(path).expanduser()
    info = probe_media(source)
    if not info.has_audio:
        raise ValueError("This media file does not contain audio.")

    vf = (
        f"aformat=channel_layouts=mono,"
        f"showwavespic=s={int(width)}x{int(height)}:colors=0x23D5FF,"
        "format=yuvj420p"
    )
    command = [
        ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        vf,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "4",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(error or "Could not render waveform.")
    return result.stdout


def _default_extension(info: MediaInfo, source: Path) -> str:
    if info.has_video:
        return ".mp4"
    suffix = source.suffix.lower()
    return suffix if suffix in {".mp3", ".m4a", ".wav"} else ".m4a"


def unique_output_path(output_dir: Path, output_name: str, info: MediaInfo, source: Path) -> Path:
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    raw = Path(safe_export_name(output_name, f"{source.stem}_edited"))
    suffix = raw.suffix.lower()
    if info.has_video:
        if suffix not in {".mp4", ".mov"}:
            suffix = ".mp4"
    else:
        if suffix not in {".mp3", ".m4a", ".wav"}:
            suffix = _default_extension(info, source)

    stem = safe_export_name(raw.stem, f"{source.stem}_edited")
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate


def build_export_command(
    path: Path,
    output: Path,
    info: MediaInfo,
    *,
    start: float,
    end: float,
    crop_preset: str,
    custom_crop: tuple[int, int, int, int] | None,
    rotate: str,
    speed: float,
    mute: bool,
    volume_percent: float,
    fade_in: float,
    fade_out: float,
    audio_preset: str,
    noise_reduction: bool,
    quality: str,
) -> tuple[list[str], float]:
    start = max(0.0, min(float(start), info.duration))
    end = max(start + 0.001, min(float(end), info.duration))
    clip_duration = max(0.001, end - start)
    speed = max(0.5, min(2.0, float(speed)))
    output_duration = clip_duration / speed

    command = [
        ffmpeg_exe(),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{clip_duration:.3f}",
        "-i",
        str(path),
    ]

    if info.has_video:
        video_filters = build_video_filters(
            info,
            crop_preset,
            rotate,
            speed,
            custom_crop=custom_crop,
        )
        command += ["-map", "0:v:0", "-vf", ",".join(video_filters)]
        crf = {"High": "18", "Balanced": "23", "Small": "28"}.get(quality, "23")
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]

        if info.has_audio and not mute:
            audio_filters = build_audio_filters(
                speed,
                volume_percent,
                fade_in,
                fade_out,
                output_duration,
                audio_preset=audio_preset,
                noise_reduction=noise_reduction,
            )
            command += ["-map", "0:a:0?"]
            if audio_filters:
                command += ["-af", ",".join(audio_filters)]
            command += ["-c:a", "aac", "-b:a", "192k"]
        else:
            command += ["-an"]

        if output.suffix.lower() in {".mp4", ".mov"}:
            command += ["-movflags", "+faststart"]
    else:
        audio_filters = build_audio_filters(
            speed,
            0.0 if mute else volume_percent,
            fade_in,
            fade_out,
            output_duration,
            audio_preset=audio_preset,
            noise_reduction=noise_reduction,
        )
        if audio_filters:
            command += ["-af", ",".join(audio_filters)]
        if output.suffix.lower() == ".mp3":
            command += ["-c:a", "libmp3lame", "-b:a", "192k"]
        elif output.suffix.lower() == ".wav":
            command += ["-c:a", "pcm_s16le"]
        else:
            command += ["-c:a", "aac", "-b:a", "192k"]

    command += ["-progress", "pipe:1", "-nostats", str(output)]
    return command, output_duration


def render_proxy_clip(
    path: Path,
    *,
    start: float,
    duration: float,
    crop_preset: str,
    custom_crop: tuple[int, int, int, int] | None,
    rotate: str,
    speed: float,
    mute: bool,
    volume_percent: float,
    audio_preset: str = "Flat",
    noise_reduction: bool = False,
    timeout: float = 45.0,
) -> bytes:
    source = Path(path).expanduser()
    info = probe_media(source)
    if not info.has_video:
        raise ValueError("Playable proxy preview requires a video stream.")

    start = max(0.0, min(float(start), info.duration))
    duration = max(0.75, min(float(duration), 10.0, max(0.75, info.duration - start)))
    speed = max(0.5, min(2.0, float(speed)))

    with tempfile.TemporaryDirectory(prefix="MediaDownloaderPreview_") as folder:
        output = Path(folder) / "preview.mp4"
        command = [
            ffmpeg_exe(),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
        ]

        filters = build_video_filters(
            info,
            crop_preset,
            rotate,
            speed,
            custom_crop=custom_crop,
            include_speed=True,
            preview_size=(640, 360),
        )
        command += [
            "-map",
            "0:v:0",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "29",
            "-pix_fmt",
            "yuv420p",
        ]

        if info.has_audio and not mute:
            audio_filters = build_audio_filters(
                speed,
                volume_percent,
                0.0,
                0.0,
                duration / speed,
                audio_preset=audio_preset,
                noise_reduction=noise_reduction,
            )
            command += ["-map", "0:a:0?"]
            if audio_filters:
                command += ["-af", ",".join(audio_filters)]
            command += ["-c:a", "aac", "-b:a", "96k"]
        else:
            command += ["-an"]

        command += ["-movflags", "+faststart", str(output)]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            error = result.stderr.decode("utf-8", errors="replace")[-1600:]
            raise RuntimeError(error or "Could not render playable preview clip.")
        return output.read_bytes()


def _reader(stream, output_queue: queue.Queue[tuple[str, str]], name: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put((name, line.rstrip("\r\n")))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def run_export(
    path: Path,
    *,
    output_dir: Path,
    output_name: str,
    start: float,
    end: float,
    crop_preset: str,
    custom_crop: tuple[int, int, int, int] | None,
    rotate: str,
    speed: float,
    mute: bool,
    volume_percent: float,
    fade_in: float,
    fade_out: float,
    audio_preset: str,
    noise_reduction: bool,
    quality: str,
    cancel_event: threading.Event,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
) -> Path:
    source = Path(path).expanduser()
    info = probe_media(source)
    if start < 0 or end <= start or end > info.duration + 0.05:
        raise ValueError("Trim range is outside the source duration.")

    output = unique_output_path(Path(output_dir), output_name, info, source)
    temporary = output.with_name(f"{output.stem}.part{output.suffix}")
    command, output_duration = build_export_command(
        source,
        temporary,
        info,
        start=start,
        end=end,
        crop_preset=crop_preset,
        custom_crop=custom_crop,
        rotate=rotate,
        speed=speed,
        mute=mute,
        volume_percent=volume_percent,
        fade_in=fade_in,
        fade_out=fade_out,
        audio_preset=audio_preset,
        noise_reduction=noise_reduction,
        quality=quality,
    )

    if on_status:
        on_status("Starting FFmpeg export")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        bufsize=1,
    )
    output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(target=_reader, args=(process.stdout, output_queue, "stdout"), daemon=True).start()
    threading.Thread(target=_reader, args=(process.stderr, output_queue, "stderr"), daemon=True).start()

    try:
        while process.poll() is None:
            if cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise EditorCancelled("Export cancelled.")

            try:
                stream_name, line = output_queue.get(timeout=0.12)
            except queue.Empty:
                continue

            if stream_name == "stderr":
                if line:
                    stderr_lines.append(line)
                    stderr_lines[:] = stderr_lines[-20:]
                continue

            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    elapsed = float(value) / 1_000_000.0
                    progress = elapsed / max(0.001, output_duration)
                    if on_progress:
                        on_progress(max(0.0, min(0.99, progress)), "Exporting")
                except ValueError:
                    pass
            elif key == "progress" and value == "end" and on_progress:
                on_progress(1.0, "Finalizing")

        # Drain remaining stderr lines after exit.
        time.sleep(0.03)
        while True:
            try:
                stream_name, line = output_queue.get_nowait()
            except queue.Empty:
                break
            if stream_name == "stderr" and line:
                stderr_lines.append(line)

        if process.returncode != 0:
            raise RuntimeError("\n".join(stderr_lines[-12:]) or f"FFmpeg exited with code {process.returncode}.")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("FFmpeg completed without producing an export file.")

        temporary.replace(output)
        if on_progress:
            on_progress(1.0, "Complete")
        if on_status:
            on_status("Export complete")
        return output
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
