from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

import screen_recorder_engine as engine
from media_editor_engine import ffmpeg_exe
from screen_recorder_engine import AudioTrack, Monitor, RecordSettings


def _ffmpeg_available() -> bool:
    try:
        return bool(ffmpeg_exe()) and Path(ffmpeg_exe()).exists()
    except Exception:
        return False


def settings(**overrides) -> RecordSettings:
    values = dict(region=(100, 50, 1280, 720), output_dir=Path("out"))
    values.update(overrides)
    return RecordSettings(**values)


class RegionTests(unittest.TestCase):
    def test_normalize_makes_even_and_positive(self) -> None:
        self.assertEqual(engine.normalize_region((10, 10, 641, 361)), (10, 10, 640, 360))
        self.assertEqual(engine.normalize_region((700, 500, -641, -361)), (59, 139, 640, 360))

    def test_normalize_clamps_into_bounds(self) -> None:
        self.assertEqual(engine.normalize_region((1800, -20, 400, 300), (0, 0, 1920, 1080)), (1800, 0, 120, 300))
        # Negative coordinates: a screen left of the main one.
        self.assertEqual(engine.normalize_region((-1920, 0, 1920, 1080), (-1920, 0, 3840, 1080)),
                         (-1920, 0, 1920, 1080))

    def test_output_size_never_upscales(self) -> None:
        self.assertEqual(engine.output_size((0, 0, 2560, 1440), "1080p"), (1920, 1080))
        self.assertEqual(engine.output_size((0, 0, 1280, 720), "1080p"), (1280, 720))
        self.assertEqual(engine.output_size((0, 0, 1366, 768), "720p"), (1280, 720))

    def test_monitor_for_region(self) -> None:
        monitors = [Monitor(0, 0, 0, 1920, 1080, True), Monitor(1, 1920, 0, 1280, 1024)]
        self.assertEqual(engine.monitor_for_region(monitors, (2000, 10, 300, 300)).index, 1)
        self.assertIsNone(engine.monitor_for_region(monitors, (1800, 10, 300, 300)))
        self.assertEqual(engine.virtual_bounds(monitors), (0, 0, 3200, 1080))


class CaptureCommandTests(unittest.TestCase):
    def test_gdigrab_region(self) -> None:
        command = engine.build_capture_command(settings(fps=60, draw_mouse=False), Path("p.mkv"),
                                               engine="gdigrab", encoder="x264")
        text = " ".join(command)
        self.assertIn("-f gdigrab", text)
        self.assertIn("-framerate 60", text)
        self.assertIn("-draw_mouse 0", text)
        self.assertIn("-offset_x 100 -offset_y 50 -video_size 1280x720 -i desktop", text)
        self.assertIn("-preset ultrafast", text)  # 60 fps: keep up in real time
        self.assertIn("-progress pipe:1", text)
        self.assertEqual(command[-3:], ["-f", "matroska", "p.mkv"])
        self.assertNotIn("-nostdin", command)  # Stop sends "q" on stdin
        self.assertIn("-an", command)

    def test_quality_and_resolution(self) -> None:
        command = engine.build_capture_command(settings(region=(0, 0, 2560, 1440), quality="Small",
                                                        resolution="720p"), Path("p.mkv"),
                                               engine="gdigrab", encoder="x264")
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("scale=1280:720", graph)
        self.assertIn("format=yuv420p", graph)
        self.assertEqual(command[command.index("-crf") + 1], "28")

    def test_hardware_encoders(self) -> None:
        nvenc = engine.build_capture_command(settings(), Path("p.mkv"), engine="gdigrab", encoder="nvenc")
        self.assertIn("h264_nvenc", nvenc)
        qsv = engine.build_capture_command(settings(), Path("p.mkv"), engine="gdigrab", encoder="qsv")
        self.assertIn("h264_qsv", qsv)
        self.assertIn("format=nv12", qsv[qsv.index("-filter_complex") + 1])

    def test_ddagrab_uses_monitor_offsets(self) -> None:
        command = engine.build_capture_command(
            settings(region=(2020, 100, 640, 480), monitor_index=1, monitor_origin=(1920, 0)),
            Path("p.mkv"), engine="ddagrab", encoder="x264")
        source = command[command.index("lavfi") + 2]
        self.assertIn("output_idx=1", source)
        self.assertIn("offset_x=100:offset_y=100", source)
        self.assertIn("hwdownload,format=bgra", command[command.index("-filter_complex") + 1])

    def test_webcam_overlay(self) -> None:
        command = engine.build_capture_command(settings(webcam="HD Webcam", webcam_position="Top left",
                                                        webcam_size="Medium"), Path("p.mkv"),
                                               engine="gdigrab", encoder="x264")
        self.assertIn("video=HD Webcam", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[1:v]scale=320:-2[cam]", graph)
        self.assertIn("overlay=14:14", graph)

    def test_resolve_encoder(self) -> None:
        self.assertEqual(engine.resolve_encoder("auto", ["x264", "qsv"]), "qsv")
        self.assertEqual(engine.resolve_encoder("nvenc", ["x264"]), "x264")
        self.assertEqual(engine.resolve_encoder("x264", ["x264", "nvenc"]), "x264")


class MuxTests(unittest.TestCase):
    def test_no_audio_copies(self) -> None:
        command = engine.build_part_mux_command(Path("v.mkv"), [], Path("o.mkv"))
        self.assertIn("copy", command)
        self.assertNotIn("-filter_complex", command)

    def test_audio_alignment_and_mix(self) -> None:
        command = engine.build_part_mux_command(
            Path("v.mkv"), [AudioTrack(Path("s.wav"), 0.25), AudioTrack(Path("m.wav"), -0.1, 1.5)], Path("o.mkv"))
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[1:a]adelay=250:all=1", graph)
        self.assertIn("[2:a]atrim=start=0.100", graph)
        self.assertIn("volume=1.500", graph)
        # No length known: no padding at all (apad without an end never finishes).
        self.assertIn("amix=inputs=2:duration=longest:normalize=0[aout]", graph)
        self.assertNotIn("apad", graph)
        self.assertNotIn("-shortest", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")

    def test_padding_always_has_an_end(self) -> None:
        command = engine.build_part_mux_command(
            Path("v.mkv"), [AudioTrack(Path("s.wav"), 0.25)], Path("o.mkv"), duration=12.5)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("apad=whole_dur=12.500[aout]", graph)
        self.assertEqual(command[command.index("-t") + 1], "12.500")
        self.assertNotIn("-shortest", command)

    def test_part_length_uses_the_earlier_end(self) -> None:
        self.assertEqual(engine.part_length(10.0, 9.4), 9.4)
        self.assertEqual(engine.part_length(None, 9.4), 9.4)
        self.assertEqual(engine.part_length(10.0, None), 10.0)
        self.assertIsNone(engine.part_length(None, None))
        self.assertIsNone(engine.part_length(0.0, -1.0))

    def test_duration_parsing(self) -> None:
        self.assertAlmostEqual(engine.parse_duration("  Duration: 00:01:02.50, start: 0.0"), 62.5)
        self.assertIsNone(engine.parse_duration("  Duration: N/A, start: 0.0"))
        log = "frame=1 time=00:00:01.00 bitrate\nframe=9 time=00:00:04.20 bitrate"
        self.assertAlmostEqual(engine.parse_last_time(log), 4.2)


@unittest.skipUnless(shutil.which("ffmpeg") or _ffmpeg_available(), "ffmpeg is not available")
class RealMuxTests(unittest.TestCase):
    """Runs the real ffmpeg: the part with sound must finish (it used to run forever)."""

    def _make(self, folder: Path) -> tuple[Path, Path]:
        video, audio = folder / "v.mkv", folder / "a.wav"
        ff = ffmpeg_exe()
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=s=160x120:r=15:d=3", "-c:v", "libx264", "-f", "matroska", str(video)], check=True,
                       timeout=60)
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1.5",
                        "-ar", "48000", "-ac", "2", str(audio)], check=True, timeout=60)
        return video, audio

    def test_mux_finishes_and_keeps_the_video_length(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            video, audio = self._make(folder)
            length = engine.probe_duration(video)
            self.assertIsNotNone(length)
            self.assertAlmostEqual(length, 3.0, delta=0.3)
            for duration in (length, None):
                output = folder / f"o{duration}.mkv"
                command = engine.build_part_mux_command(
                    video, [AudioTrack(audio, 0.4), AudioTrack(audio, -0.2, 0.5)], output, duration)
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertAlmostEqual(engine.probe_duration(output), 3.0, delta=0.35)

    def test_concat_list_escapes(self) -> None:
        text = engine.concat_list_text([Path("C:/Rec/it's.mkv")])
        self.assertEqual(text, "file 'C:/Rec/it'\\''s.mkv'\n")


class ParsingTests(unittest.TestCase):
    def test_capture_start_wall_clock(self) -> None:
        line = "  Duration: N/A, start: 1794949616.662669, bitrate: 115200 kb/s"
        value = engine.capture_start_from_log(line, wall_now=1794949617.0, mono_now=500.0)
        self.assertAlmostEqual(value, 500.0 - (1794949617.0 - 1794949616.662669), places=5)

    def test_capture_start_monotonic(self) -> None:
        line = "  Duration: N/A, start: 499.5, bitrate: N/A"
        self.assertAlmostEqual(engine.capture_start_from_log(line, 1794949617.0, 500.0), 499.5)
        self.assertIsNone(engine.capture_start_from_log("  Duration: N/A, start: 0.000000", 1.7e9, 500.0))

    def test_dshow_listing_new_format(self) -> None:
        text = '''[dshow @ 0x1] "Integrated Camera" (video)
[dshow @ 0x1]   Alternative name "@device_pnp_xyz"
[dshow @ 0x1] "Microphone (Realtek)" (audio)
[dshow @ 0x1]   Alternative name "@device_cm_abc"'''
        self.assertEqual(engine.parse_dshow_devices(text), (["Integrated Camera"], ["Microphone (Realtek)"]))

    def test_dshow_listing_old_format(self) -> None:
        text = '''[dshow @ 0x1] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0x1]  "USB Camera"
[dshow @ 0x1] DirectShow audio devices
[dshow @ 0x1]  "Line In"'''
        self.assertEqual(engine.parse_dshow_devices(text), (["USB Camera"], ["Line In"]))

    def test_formatting(self) -> None:
        self.assertEqual(engine.format_duration(3725.4), "01:02:05")
        self.assertEqual(engine.format_size(1536), "2 KB")
        self.assertEqual(engine.format_size(5 * 1024 * 1024), "5.0 MB")


if __name__ == "__main__":
    unittest.main()
