from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from hybrid_core.diagnostics import create_diagnostics_bundle, sanitize


class DiagnosticsTests(unittest.TestCase):
    def test_sanitize_redacts_urls_and_paths(self):
        value = sanitize(
            {
                "url": "https://example.com/private/video?id=123",
                "download_dir": "C:/Users/Test/Videos",
                "nested": {"source_path": "D:/Secret/source.mp4"},
            }
        )
        self.assertEqual(value["url"], "https://example.com/…")
        self.assertTrue(str(value["download_dir"]).endswith("/Videos"))
        self.assertTrue(str(value["nested"]["source_path"]).endswith("/source.mp4"))

    def test_bundle_contains_sanitized_summary_and_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / "hybrid-core.log"
            log.write_text('{"message":"demo"}\n', encoding="utf-8")

            bundle = create_diagnostics_bundle(
                output_dir=root / "diagnostics",
                log_file=log,
                settings={"download_dir": "C:/Users/Test/Videos"},
                jobs=[{"request": {"url": "https://example.com/private?id=1"}, "status": "failed"}],
                diagnostics={"version": "0.9.0"},
                app_data_dir=root,
            )

            self.assertTrue(bundle.is_file())
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn("diagnostics.json", names)
                self.assertIn("logs/hybrid-core.log", names)
                summary = json.loads(archive.read("diagnostics.json").decode("utf-8"))
                self.assertEqual(summary["jobs"][0]["request"]["url"], "https://example.com/…")


if __name__ == "__main__":
    unittest.main()
