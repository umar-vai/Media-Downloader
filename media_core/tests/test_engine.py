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
