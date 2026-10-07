from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from imageio_ffmpeg import get_ffmpeg_exe


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def ffmpeg_exe() -> str:
    return get_ffmpeg_exe()


def format_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def parse_time(value: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError("Time cannot be empty.")
    if ":" not in text:
        return max(0.0, float(text))
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("Use SS, MM:SS or HH:MM:SS.")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Invalid time value.") from exc
    if len(numbers) == 2:
        return max(0.0, numbers[0] * 60 + numbers[1])
    if len(numbers) == 3:
        return max(0.0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
    return max(0.0, numbers[0])


def even_size(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def safe_export_name(value: str, fallback: str = "edited_media") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:160] or fallback).strip()


def probe_media(path: Path) -> MediaInfo:
    command = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(path)]
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

    video_line = next((line for line in text.splitlines() if " Video: " in line), "")
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


def compute_crop(
    info: MediaInfo,
    preset: str,
    custom: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int] | None:
    if not info.has_video or preset == "Original":
        return None
    if info.width <= 0 or info.height <= 0:
        raise ValueError("Video dimensions could not be detected.")

    if preset == "Custom":
        if custom is None:
            raise ValueError("Custom crop needs X, Y, width and height.")
        x, y, width, height = custom
        x = max(0, int(x))
        y = max(0, int(y))
        width = even_size(width)
        height = even_size(height)
        if x + width > info.width or y + height > info.height:
            raise ValueError("Custom crop is outside the source frame.")
        return x, y, width, height

    ratio = CROP_PRESETS.get(preset)
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
    custom_crop: tuple[int, int, int, int] | None,
    rotate: str,
    speed: float,
    *,
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

    if include_speed and abs(speed - 1.0) > 0.0001:
        filters.append(f"setpts=PTS/{speed:g}")

    if preview_size:
        width, height = preview_size
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    return filters


def build_audio_filters(
    speed: float,
    volume_percent: float,
    fade_in: float,
    fade_out: float,
    output_duration: float,
    *,
    include_fades: bool = True,
) -> list[str]:
    filters: list[str] = []
    if abs(speed - 1.0) > 0.0001:
        filters.append(f"atempo={speed:g}")

    volume = max(0.0, float(volume_percent)) / 100.0
    if abs(volume - 1.0) > 0.0001:
        filters.append(f"volume={volume:.3f}")

    if include_fades:
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
    filters: list[str],
    *,
    timeout: float = 10.0,
) -> bytes:
    command = [
        ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, position):.3f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-threads",
        "1",
    ]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "pipe:1"]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(error or "Could not render the preview frame.")
    return result.stdout


def extract_waveform(
    path: Path,
    *,
    width: int = 800,
    height: int = 450,
    timeout: float = 15.0,
) -> bytes:
    vf = (
        f"aformat=channel_layouts=mono,"
        f"showwavespic=s={width}x{height}:colors=0x23D5FF,"
        "format=yuvj420p"
    )
    command = [
        ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
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
        raise RuntimeError(error or "Could not render the audio waveform.")
    return result.stdout


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
    quality: str,
) -> tuple[list[str], float]:
    clip_duration = max(0.001, end - start)
    output_duration = clip_duration / max(0.01, speed)

    command = [
        ffmpeg_exe(),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(path),
        "-t",
        f"{clip_duration:.3f}",
    ]

    if info.has_video:
        video_filters = build_video_filters(
            info,
            crop_preset,
            custom_crop,
            rotate,
            speed,
            include_speed=True,
        )
        command += ["-map", "0:v:0", "-vf", ",".join(video_filters)]

        crf = {
            "High": "18",
            "Balanced": "23",
            "Small": "28",
        }.get(quality, "18")
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]

        if info.has_audio and not mute:
            audio_filters = build_audio_filters(
                speed,
                volume_percent,
                fade_in,
                fade_out,
                output_duration,
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
            volume_percent,
            fade_in,
            fade_out,
            output_duration,
        )
        if audio_filters:
            command += ["-af", ",".join(audio_filters)]
        suffix = output.suffix.lower()
        if suffix == ".mp3":
            command += ["-c:a", "libmp3lame", "-b:a", "192k"]
        elif suffix == ".m4a":
            command += ["-c:a", "aac", "-b:a", "192k"]
        elif suffix == ".wav":
            command += ["-c:a", "pcm_s16le"]

    command += ["-progress", "pipe:1", "-nostats", str(output)]
    return command, output_duration


def build_preview_clip_command(
    path: Path,
    output: Path,
    info: MediaInfo,
    *,
    start: float,
    duration: float,
    crop_preset: str,
    custom_crop: tuple[int, int, int, int] | None,
    rotate: str,
    speed: float,
    mute: bool,
    volume_percent: float,
    fade_in: float,
    fade_out: float,
) -> list[str]:
    end = min(info.duration, start + max(0.5, duration))
    command, _ = build_export_command(
        path,
        output,
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
        quality="Balanced",
    )
    # Preview generation is not parsed for progress, so remove progress output.
    progress_index = command.index("-progress")
    command = command[:progress_index] + command[progress_index + 4 :]
    return command
