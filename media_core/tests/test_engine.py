from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from media_core.engine import Cancelled, analyze_url, safe_filename


class MediaCoreEngineTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('A: bad / name?.mp4'), 'A_ bad _ name')

    @patch("media_core.engine.resolve_with_installed_browser")
    @patch("media_core.engine.extraction_attempts")
    def test_browser_fallback_is_used_after_extractor_failure(self, attempts, browser):
        attempts.return_value = []
        browser.return_value = {
            "title": "Fallback media",
            "formats": [{"url": "https://cdn.example/video.mp4", "height": 720}],
        }
        summary, info = analyze_url(
            "https://example.com/watch/1",
            cancel_event=threading.Event(),
        )
        self.assertEqual(summary["title"], "Fallback media")
        self.assertEqual(info["formats"][0]["height"], 720)

    def test_cancelled_analysis_stops_before_network(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            analyze_url("https://example.com/watch/1", cancel_event=event)


if __name__ == "__main__":
    unittest.main()


class DownloadEndToEndTests(unittest.TestCase):
    """Runs a real yt-dlp download against a local HLS stream, so an engine call that
    no longer exists in yt-dlp (e.g. a renamed method) fails here, not for users."""

    def test_local_hls_download(self):
        import functools
        import http.server
        import subprocess
        import tempfile
        from pathlib import Path

        from media_core.engine import download_from_analysis, get_ffmpeg_exe

        ffmpeg = get_ffmpeg_exe()
        if not ffmpeg:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as tmp:
            srv, out = Path(tmp, "srv"), Path(tmp, "out")
            srv.mkdir()
            subprocess.run(
                [ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "testsrc=duration=2:size=160x120:rate=15", "-c:v", "libx264",
                 "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod", str(srv / "t.m3u8")],
                check=True,
            )

            class Quiet(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass

            server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv))
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                path = download_from_analysis(
                    url=f"http://127.0.0.1:{server.server_port}/t.m3u8",
                    cached_info={},
                    download_dir=out,
                    filename="e2e",
                    mode="video",
                    video_quality="Best available",
                    audio_format="mp3",
                    audio_quality="192",
                    cancel_event=threading.Event(),
                )
            finally:
                server.shutdown()
                server.server_close()
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)


class HlsProgressTests(unittest.TestCase):
    """yt-dlp's byte total for HLS is a guess from the first fragments. It used to drive the
    percentage, so a 1.2 GB video sat at 98.5% after 60 MB. Fragments must drive it now."""

    def _run(self, events):
        import tempfile
        from pathlib import Path

        from media_core import engine

        progress: list[float] = []
        metrics: list[dict] = []

        class FakeYDL:
            def __init__(self, params):
                self.params = params
                self._pps = {"post_process": []}

            def add_post_processor(self, pp, when="post_process"):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def process_ie_result(self, info, download=True):
                for data in events:
                    for hook in self.params["progress_hooks"]:
                        hook(dict(data))
                Path(self.params["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"x" * 10)

        with tempfile.TemporaryDirectory() as tmp, patch.object(engine.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download_from_analysis(
                url="https://example.com/master.m3u8",
                cached_info={"title": "t", "formats": [{"format_id": "hls", "url": "https://example.com/master.m3u8"}]},
                download_dir=Path(tmp),
                filename="t",
                mode="video",
                video_quality="Best available",
                audio_format="mp3",
                audio_quality="192",
                cancel_event=threading.Event(),
                on_progress=lambda value, detail: progress.append(value),
                on_metrics=metrics.append,
            )
        return progress, metrics

    def test_fragment_count_drives_percentage(self):
        info = {"format_id": "hls"}
        progress, metrics = self._run([
            # 60 MB in, but the estimate wrongly says the file is only 61 MB.
            {"status": "downloading", "info_dict": info, "downloaded_bytes": 60_000_000,
             "total_bytes_estimate": 61_000_000, "fragment_index": 5, "fragment_count": 100, "speed": 2e6},
            {"status": "downloading", "info_dict": info, "downloaded_bytes": 600_000_000,
             "total_bytes_estimate": 1_200_000_000, "fragment_index": 50, "fragment_count": 100, "speed": 2e6},
        ])
        self.assertAlmostEqual(progress[0], 0.05, places=3)
        self.assertAlmostEqual(progress[1], 0.50, places=3)
        self.assertTrue(metrics[0]["total_is_estimate"])

    def test_bare_estimate_never_reaches_finishing(self):
        info = {"format_id": "x"}
        progress, metrics = self._run([
            {"status": "downloading", "info_dict": info, "downloaded_bytes": 99, "total_bytes_estimate": 100},
        ])
        self.assertLessEqual(progress[0], 0.9)
        self.assertTrue(metrics[0]["total_is_estimate"])

    def test_exact_total_is_not_marked_estimate(self):
        info = {"format_id": "x"}
        progress, metrics = self._run([
            {"status": "downloading", "info_dict": info, "downloaded_bytes": 50, "total_bytes": 100},
        ])
        self.assertAlmostEqual(progress[0], 0.5, places=3)
        self.assertFalse(metrics[0]["total_is_estimate"])
