from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from app import default_settings, normalize_settings


class SettingsNormalizationTests(unittest.TestCase):
    def test_defaults_are_complete(self) -> None:
        settings = default_settings()
        self.assertEqual(settings["default_mode"], "Video")
        self.assertEqual(settings["video_quality"], "720p")
        self.assertEqual(settings["audio_format"], "MP3")
        self.assertEqual(settings["audio_quality"], "192")
        self.assertTrue(settings["auto_analyze_links"])
        self.assertTrue(settings["confirm_before_exit"])

    def test_invalid_values_fall_back_safely(self) -> None:
        settings = normalize_settings(
            {
                "default_mode": "SomethingElse",
                "video_quality": "16K",
                "audio_format": "FLAC",
                "audio_quality": "999",
                "auto_check_updates": "yes",
                "auto_download_updates": 1,
                "auto_analyze_links": None,
                "open_editor_after_download": "false",
                "confirm_before_exit": 0,
                "snooze_until": "not-a-number",
            }
        )
        self.assertEqual(settings["default_mode"], "Video")
        self.assertEqual(settings["video_quality"], "720p")
        self.assertEqual(settings["audio_format"], "MP3")
        self.assertEqual(settings["audio_quality"], "192")
        self.assertTrue(settings["auto_check_updates"])
        self.assertFalse(settings["auto_download_updates"])
        self.assertTrue(settings["auto_analyze_links"])
        self.assertFalse(settings["open_editor_after_download"])
        self.assertTrue(settings["confirm_before_exit"])
        self.assertEqual(settings["snooze_until"], 0)

    def test_valid_preferences_are_preserved(self) -> None:
        settings = normalize_settings(
            {
                "default_mode": "Audio",
                "video_quality": "Best available",
                "audio_format": "M4A",
                "audio_quality": "320",
                "auto_check_updates": False,
                "auto_download_updates": True,
                "auto_analyze_links": False,
                "open_editor_after_download": True,
                "confirm_before_exit": False,
            }
        )
        self.assertEqual(settings["default_mode"], "Audio")
        self.assertEqual(settings["video_quality"], "Best available")
        self.assertEqual(settings["audio_format"], "M4A")
        self.assertEqual(settings["audio_quality"], "320")
        self.assertFalse(settings["auto_check_updates"])
        self.assertTrue(settings["auto_download_updates"])
        self.assertFalse(settings["auto_analyze_links"])
        self.assertTrue(settings["open_editor_after_download"])
        self.assertFalse(settings["confirm_before_exit"])


if __name__ == "__main__":
    unittest.main()
