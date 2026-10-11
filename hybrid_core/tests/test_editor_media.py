from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hybrid_core.editor_media import EditorMediaRegistry


class EditorMediaRegistryTests(unittest.TestCase):
    def test_register_and_resolve_local_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "video.mp4"
            source.write_bytes(b"demo")
            registry = EditorMediaRegistry(ttl_seconds=300, max_entries=8)

            token = registry.register(source)

            self.assertGreater(len(token), 20)
            self.assertEqual(registry.resolve(token), source.resolve())
            self.assertIsNone(registry.resolve("not-a-real-token"))

    def test_missing_source_is_rejected(self):
        registry = EditorMediaRegistry()
        with self.assertRaises(FileNotFoundError):
            registry.register(Path("definitely-missing-file.mp4"))


if __name__ == "__main__":
    unittest.main()
