from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hybrid_core.settings_store import SettingsStore, normalize_settings


class SettingsStoreTests(unittest.TestCase):
    def test_normalization(self):
        settings = normalize_settings(
            {
                "max_concurrent_downloads": 99,
                "video_quality": "bad",
                "audio_format": "ogg",
                "update_channel": "nightly",
                "update_policy": "dangerous",
                "update_install_hour": 99,
            },
            Path("C:/Downloads"),
        )
        self.assertEqual(settings["max_concurrent_downloads"], 6)
        self.assertEqual(settings["video_quality"], "720p")
        self.assertEqual(settings["audio_format"], "MP3")
        self.assertEqual(settings["update_channel"], "stable")
        self.assertEqual(settings["update_policy"], "notify")
        self.assertEqual(settings["update_install_hour"], 23)
        self.assertTrue(settings["tray_icon_enabled"])
        self.assertFalse(settings["launch_at_login"])

    def test_persists_separately_from_legacy_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hybrid-settings.json"
            store = SettingsStore(path, default_download_dir=Path(folder) / "Downloads")
            updated, restart_required = store.update(
                {
                    "download_dir": str(Path(folder) / "Media"),
                    "max_concurrent_downloads": 4,
                    "auto_check_core_updates": False,
                }
            )
            self.assertTrue(restart_required)
            self.assertEqual(updated["max_concurrent_downloads"], 4)

            restored = SettingsStore(path, default_download_dir=Path(folder) / "Other")
            self.assertEqual(restored.get()["max_concurrent_downloads"], 4)
            self.assertFalse(restored.get()["auto_check_core_updates"])
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["download_dir"], str(Path(folder) / "Media"))


if __name__ == "__main__":
    unittest.main()
