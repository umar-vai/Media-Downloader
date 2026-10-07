from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_mode


class InstallModeTests(unittest.TestCase):
    def test_source_mode_when_not_frozen(self) -> None:
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(install_mode.install_mode_name(), "source")
            self.assertFalse(install_mode.is_installed_mode())

    def test_installer_mode_requires_marker_next_to_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "MediaDownloader.exe"
            exe.write_bytes(b"")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(exe)):
                self.assertEqual(install_mode.install_mode_name(), "portable")
                (Path(directory) / install_mode.INSTALL_MARKER).write_text("installer-managed\n", encoding="utf-8")
                self.assertTrue(install_mode.is_installed_mode())
                self.assertEqual(install_mode.install_mode_name(), "installer")


if __name__ == "__main__":
    unittest.main()
