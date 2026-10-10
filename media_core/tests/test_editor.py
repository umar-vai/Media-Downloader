from __future__ import annotations

import unittest
from pathlib import Path

from media_core.editor import (
    MediaInfo,
    build_audio_filters,
    build_export_command,
    compute_crop,
    safe_export_name,
)


class EditorEngineTests(unittest.TestCase):
    def test_safe_export_name(self):
        self.assertEqual(safe_export_name('bad:name?.mp4'), 'bad_name_.mp4')

    def test_center_crop_9_16(self):
        info = MediaInfo(duration=10, has_video=True, has_audio=True, width=1920, height=1080, fps=30)
        x, y, width, height = compute_crop(info, "9:16")
        self.assertEqual(height, 1080)
        self.assertEqual(width % 2, 0)
        self.assertEqual(x, (1920 - width) // 2)
        self.assertEqual(y, 0)

    def test_custom_crop(self):
        info = MediaInfo(duration=10, has_video=True, has_audio=False, width=1920, height=1080, fps=30)
        self.assertEqual(compute_crop(info, "Custom", (100, 50, 800, 600)), (100, 50, 800, 600))
        with self.assertRaises(ValueError):
            compute_crop(info, "Custom", (1800, 0, 400, 400))

    def test_audio_filters(self):
        filters = build_audio_filters(
            2.0,
            50,
            1.0,
            2.0,
            5.0,
            audio_preset="Voice Clarity",
            noise_reduction=True,
        )
        self.assertIn("atempo=2", filters)
        self.assertIn("volume=0.500", filters)
        self.assertTrue(any(item.startswith("afade=t=in") for item in filters))
        self.assertTrue(any(item.startswith("afade=t=out") for item in filters))
        self.assertIn("afftdn=nf=-25", filters)
        self.assertIn("highpass=f=80", filters)

    def test_export_command_contains_trim_crop_rotate_and_progress(self):
        info = MediaInfo(duration=60, has_video=True, has_audio=True, width=1920, height=1080, fps=30)
        command, duration = build_export_command(
            Path("source.mp4"),
            Path("output.mp4"),
            info,
            start=5,
            end=25,
            crop_preset="1:1",
            custom_crop=None,
            rotate="90°",
            speed=2,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            audio_preset="Flat",
            noise_reduction=False,
            quality="Balanced",
        )
        joined = " ".join(command)
        self.assertIn("-ss 5.000", joined)
        self.assertIn("-t 20.000", joined)
        self.assertIn("crop=", joined)
        self.assertIn("transpose=1", joined)
        self.assertIn("setpts=PTS/2", joined)
        self.assertIn("-progress pipe:1", joined)
        self.assertAlmostEqual(duration, 10.0)


if __name__ == "__main__":
    unittest.main()
