from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from media_core import engine  # noqa: E402


class SafeRetryTests(unittest.TestCase):
    def test_output_error_retries_without_tags_then_with_plain_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            seen: list[tuple[str, int]] = []

            def fake(**kwargs):
                mode = kwargs.get("_safe_mode", 0)
                seen.append((kwargs["filename"], mode))
                if mode < 2:
                    raise RuntimeError("No compatible download path succeeded.\nError opening output files: Invalid argument")
                out = folder / (kwargs["filename"] + ".mp4")
                out.write_bytes(b"x")
                return out

            with mock.patch.object(engine, "_download_from_analysis", fake):
                result = engine.download_from_analysis(
                    url="u", cached_info={}, download_dir=folder, filename="Nice Name", mode="Video",
                    video_quality="Best available", audio_format="mp3", audio_quality="192",
                    cancel_event=threading.Event())
            self.assertEqual(result.name, "Nice Name.mp4")
            self.assertTrue(result.exists())
            self.assertEqual([m for _n, m in seen], [0, 1, 2])
            self.assertTrue(seen[2][0].startswith("dl_"))

    def test_plain_name_is_the_same_on_every_retry(self) -> None:
        # A random name made every auto-retry download the whole video again.
        names: list[str] = []

        def fake(**kwargs):
            if kwargs.get("_safe_mode", 0) < 2:
                raise RuntimeError("Error opening output files: Invalid argument")
            names.append(kwargs["filename"])
            raise RuntimeError("Error opening output files: Invalid argument")

        with mock.patch.object(engine, "_download_from_analysis", fake):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    engine.download_from_analysis(
                        url="u?token=1", cached_info={}, download_dir=Path("."), filename="Show Ep 1",
                        mode="Video", video_quality="b", audio_format="mp3", audio_quality="1",
                        cancel_event=threading.Event())
        self.assertEqual(len(names), 2)
        self.assertEqual(names[0], names[1])

    def test_other_errors_are_not_retried(self) -> None:
        calls = []

        def fake(**kwargs):
            calls.append(1)
            raise RuntimeError("HTTP 404")

        with mock.patch.object(engine, "_download_from_analysis", fake):
            with self.assertRaises(RuntimeError):
                engine.download_from_analysis(
                    url="u", cached_info={}, download_dir=Path("."), filename="x", mode="Video",
                    video_quality="b", audio_format="mp3", audio_quality="1", cancel_event=threading.Event())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
