from __future__ import annotations

import subprocess
import tempfile
import wave
from array import array
from pathlib import Path
import sys

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from media_editor_engine import MediaInfo, build_export_command, ffmpeg_exe, probe_media


def run(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-3000:])


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        source_video = root / "source.mp4"
        run(
            [
                ffmpeg_exe(),
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x180:rate=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(source_video),
            ]
        )

        source_info = probe_media(source_video)
        slow_video = root / "slow.mp4"
        command, expected_duration = build_export_command(
            source_video,
            slow_video,
            source_info,
            start=0.0,
            end=2.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=0.5,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="Balanced",
        )
        run(command)
        slow_info = probe_media(slow_video)
        if not 3.75 <= slow_info.duration <= 4.25:
            raise AssertionError(
                f"Slow-motion export duration is {slow_info.duration:.3f}s; expected about {expected_duration:.3f}s"
            )

        # Blade cut list: one ripple-deleted part and one black gap, joined with concat.
        cut_video = root / "cuts.mp4"
        command, expected_duration = build_export_command(
            source_video,
            cut_video,
            source_info,
            start=0.0,
            end=2.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=1.0,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="Balanced",
            pieces=[(0.0, 0.5, "keep"), (1.0, 1.5, "gap"), (1.5, 2.0, "keep")],
        )
        run(command)
        cut_info = probe_media(cut_video)
        if not cut_info.has_audio or not 1.35 <= cut_info.duration <= 1.65:
            raise AssertionError(
                f"Cut-list export duration is {cut_info.duration:.3f}s; expected about {expected_duration:.3f}s"
            )

        source_audio = root / "source.wav"
        run(
            [
                ffmpeg_exe(),
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=44100",
                "-t",
                "1",
                "-c:a",
                "pcm_s16le",
                str(source_audio),
            ]
        )

        audio_info = MediaInfo(
            duration=1.0,
            has_video=False,
            has_audio=True,
            width=0,
            height=0,
            fps=0.0,
        )
        muted_audio = root / "muted.wav"
        command, _ = build_export_command(
            source_audio,
            muted_audio,
            audio_info,
            start=0.0,
            end=1.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=1.0,
            mute=True,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        run(command)

        with wave.open(str(muted_audio), "rb") as handle:
            if handle.getsampwidth() != 2:
                raise AssertionError("Expected 16-bit PCM muted WAV.")
            samples = array("h")
            samples.frombytes(handle.readframes(handle.getnframes()))
        peak = max((abs(value) for value in samples), default=0)
        if peak > 1:
            raise AssertionError(f"Muted audio contains non-silent samples (peak={peak}).")

    print("Editor export smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
