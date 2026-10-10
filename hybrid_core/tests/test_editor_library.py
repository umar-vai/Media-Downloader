from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hybrid_core.editor_library import EditorLibrary


class EditorLibraryTests(unittest.TestCase):
    def test_presets_and_projects_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "editor-library.json"
            library = EditorLibrary(path)
            preset = library.save_preset(
                "Podcast",
                {
                    "speed": 1.0,
                    "audio_preset": "Podcast",
                    "noise_reduction": True,
                    "source_path": "must-not-be-in-preset",
                },
            )
            project = library.save_project(
                "Episode 01",
                {
                    "source_path": "C:/media/source.mp4",
                    "start": 4.5,
                    "end": 20.0,
                    "audio_preset": "Podcast",
                },
            )

            restored = EditorLibrary(path).snapshot()
            self.assertEqual(restored["presets"][0]["id"], preset["id"])
            self.assertEqual(restored["projects"][0]["id"], project["id"])
            self.assertNotIn("source_path", restored["presets"][0]["settings"])
            self.assertEqual(restored["projects"][0]["data"]["source_path"], "C:/media/source.mp4")

    def test_update_and_delete_items(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "editor-library.json"
            library = EditorLibrary(path)
            preset = library.save_preset("First", {"quality": "Balanced"})
            updated = library.save_preset("Updated", {"quality": "High"}, preset["id"])
            self.assertEqual(updated["id"], preset["id"])
            self.assertEqual(updated["name"], "Updated")
            self.assertTrue(library.delete_preset(preset["id"]))
            self.assertFalse(library.delete_preset(preset["id"]))
            self.assertEqual(library.snapshot()["presets"], [])


if __name__ == "__main__":
    unittest.main()
