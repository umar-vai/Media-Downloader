from __future__ import annotations

import sys
import unittest
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

    def test_safe_export_name(self) -> None:
        self.assertEqual(safe_export_name('bad:/name*?.mp4'), "bad__name__.mp4")


if __name__ == "__main__":
    unittest.main()
