from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture_dialog import CaptureApi, CaptureDialog  # noqa: E402


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.snap: dict | None = {"status": "running", "progress": 0.5, "name": "a.zip", "detail": "", "result_path": ""}
        self.metrics = {"total_bytes": 2048, "downloaded_bytes": 1024, "speed_bps": 512, "eta_seconds": 2}

    def api(self) -> CaptureApi:
        return CaptureApi(
            start=lambda n, f, later: (self.calls.append(("start", n, f, later)) or "rid1"),
            state=lambda _rid: (self.snap, self.metrics),
            pause=lambda r: self.calls.append(("pause", r)),
            resume=lambda r: self.calls.append(("resume", r)),
            cancel=lambda r: self.calls.append(("cancel", r)),
            retry=lambda r: self.calls.append(("retry", r)),
            deleted=lambda r, p: self.calls.append(("deleted", r, p)),
        )


class CaptureDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # no display on this machine
            raise unittest.SkipTest(str(exc))
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def make(self, backend: FakeBackend, folder: str, name: str = "movie.zip") -> CaptureDialog:
        return CaptureDialog(self.root, backend.api(), name=name, folder=folder, source="example.com",
                             size_bytes=2048, is_file=True)

    def test_download_now_turns_into_progress_window_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            dialog = self.make(backend, tmp)
            dialog.name_var.set("episode 5")  # user renamed it without the extension
            dialog._begin(False)
            self.assertEqual(backend.calls[0], ("start", "episode 5.zip", tmp, False))
            self.assertEqual(dialog._mode, "progress")
            self.assertTrue(dialog.winfo_exists())  # the window stays open
            dialog._refresh()
            self.assertIn("50%", dialog.state_label.cget("text"))
            dialog._pause()
            self.assertEqual(backend.calls[-1], ("pause", "rid1"))

            target = Path(tmp) / "episode 5.zip"
            target.write_bytes(b"x" * 10)
            backend.snap = {"status": "completed", "progress": 1.0, "name": "episode 5.zip", "detail": "",
                            "result_path": str(target)}
            dialog._refresh()
            self.assertEqual(dialog._mode, "ended")
            self.assertEqual(dialog.state_label.cget("text"), "Download complete.")
            self.assertEqual(dialog._last_status, "done")
            dialog._on_close()

    def test_download_later_closes_window_and_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            dialog = self.make(backend, tmp)
            dialog._begin(True)
            self.assertEqual(backend.calls[0], ("start", "movie.zip", tmp, True))
            self.assertFalse(dialog.winfo_exists())

    def test_cancel_in_prompt_starts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            dialog = self.make(backend, tmp)
            dialog._on_close()
            self.assertEqual(backend.calls, [])

    def test_empty_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            dialog = self.make(backend, tmp)
            dialog.name_var.set("   ")
            dialog._begin(False)
            self.assertEqual(backend.calls, [])
            self.assertEqual(dialog._mode, "prompt")
            dialog._on_close()


if __name__ == "__main__":
    unittest.main()
