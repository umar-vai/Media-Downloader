from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from media_editor_engine import (
    MediaInfo,
    build_audio_filters,
    build_export_command,
    build_video_filters,
    compute_crop,
    even_size,
    format_time,
    parse_time,
    probe_media,
    safe_export_name,
)


class MediaEditorEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video = MediaInfo(
            duration=336.39,
            has_video=True,
            has_audio=True,
            width=1280,
            height=720,
            fps=30.0,
        )

    def test_parse_seconds(self) -> None:
        self.assertAlmostEqual(parse_time("12.5"), 12.5)

    def test_parse_minute_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("02:03.250"), 123.25)

    def test_parse_hour_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("01:02:03.5"), 3723.5)

    def test_format_time(self) -> None:
        self.assertEqual(format_time(123.25), "02:03.250")
        self.assertEqual(format_time(3723.5), "01:02:03.500")

    def test_even_dimensions(self) -> None:
        self.assertEqual(even_size(1081), 1080)
        self.assertEqual(even_size(1080), 1080)

    def test_center_crop_vertical(self) -> None:
        crop = compute_crop(self.video, "9:16")
        self.assertIsNotNone(crop)
        x, y, width, height = crop or (0, 0, 0, 0)
        self.assertEqual(height, 720)
        self.assertEqual(width % 2, 0)
        self.assertEqual(height % 2, 0)
        self.assertGreater(x, 0)
        self.assertEqual(y, 0)

    def test_custom_crop_rejects_outside_frame(self) -> None:
        with self.assertRaises(ValueError):
            compute_crop(self.video, "Custom", (1200, 0, 400, 400))

    def test_video_filters_include_speed_and_even_scale(self) -> None:
        filters = build_video_filters(
            self.video,
            "Original",
            None,
            "90°",
            1.5,
        )
        joined = ",".join(filters)
        self.assertIn("transpose=1", joined)
        self.assertIn("setpts=PTS/1.5", joined)
        self.assertIn("trunc(iw/2)*2", joined)

    def test_audio_filters(self) -> None:
        filters = build_audio_filters(1.25, 75, 1.0, 2.0, 10.0)
        joined = ",".join(filters)
        self.assertIn("atempo=1.25", joined)
        self.assertIn("volume=0.750", joined)
        self.assertIn("afade=t=in", joined)
        self.assertIn("afade=t=out", joined)

    def test_export_command_has_progress_and_output(self) -> None:
        output = Path("edited.mp4")
        command, duration = build_export_command(
            Path("input.mp4"),
            output,
            self.video,
            start=10.0,
            end=40.0,
            crop_preset="16:9",
            custom_crop=None,
            rotate="0°",
            speed=1.0,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        self.assertAlmostEqual(duration, 30.0)
        self.assertIn("-progress", command)
        self.assertEqual(command[-1], str(output))
        self.assertIn("-nostdin", command)

    def test_slow_motion_limits_input_before_decode(self) -> None:
        command, duration = build_export_command(
            Path("input.mp4"),
            Path("slow.mp4"),
            self.video,
            start=10.0,
            end=20.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=0.5,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        self.assertAlmostEqual(duration, 20.0)
        self.assertLess(command.index("-t"), command.index("-i"))
        self.assertIn("setpts=PTS/0.5", ",".join(command))

    def test_audio_only_mute_exports_silence(self) -> None:
        audio = MediaInfo(
            duration=30.0,
            has_video=False,
            has_audio=True,
            width=0,
            height=0,
            fps=0.0,
        )
        command, _ = build_export_command(
            Path("input.mp3"),
            Path("muted.mp3"),
            audio,
            start=0.0,
            end=10.0,
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
        self.assertIn("-af", command)
        self.assertIn("volume=0.000", command[command.index("-af") + 1])

    @patch("media_editor_engine.subprocess.run")
    def test_probe_ignores_attached_cover_art(self, run_mock) -> None:
        run_mock.return_value.returncode = 1
        run_mock.return_value.stderr = """
Duration: 00:01:00.00, start: 0.000000, bitrate: 192 kb/s
Stream #0:0: Audio: mp3, 44100 Hz, stereo, fltp, 192 kb/s
Stream #0:1: Video: mjpeg, yuvj420p(pc), 600x600, 90k tbr, 90k tbn (attached pic)
"""
        info = probe_media(Path("song.mp3"))
        self.assertTrue(info.has_audio)
        self.assertFalse(info.has_video)
        self.assertEqual(info.width, 0)
        self.assertEqual(info.height, 0)

    def test_safe_export_name(self) -> None:
        self.assertEqual(safe_export_name('bad:/name*?.mp4'), "bad_name_.mp4")


if __name__ == "__main__":
    unittest.main()
