from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from media_editor_engine import (
    MediaInfo,
    atempo_chain,
    build_audio_filters,
    build_export_command,
    build_player_filters,
    color_filter,
    output_dimensions,
    build_video_filters,
    compute_crop,
    even_size,
    format_time,
    parse_time,
    probe_media,
    safe_export_name,
)


class MediaEditorExportTests(unittest.TestCase):
    def test_failed_export_preserves_existing_output_and_cleans_staging(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock
        from media_editor import MediaEditorWindow

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "already-exported.mp4"
            output.write_bytes(b"previous file")
            failed = Mock()
            fake = SimpleNamespace(export_cancelled=False, _post_ui=Mock(), _export_failed=failed)
            command = [
                sys.executable, "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'incomplete'); sys.exit(1)",
                str(output),
            ]
            MediaEditorWindow._export_worker(fake, command, output, 2.0)
            self.assertEqual(output.read_bytes(), b"previous file")
            self.assertEqual(list(Path(tmp).iterdir()), [output])
            self.assertIs(fake._post_ui.call_args.args[0], failed)


class MediaEditorEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video = MediaInfo(
            duration=336.39,
            has_video=True,
            has_audio=True,
            width=1280,
            height=720,
            fps=30.0,
        )

    def test_parse_seconds(self) -> None:
        self.assertAlmostEqual(parse_time("12.5"), 12.5)

    def test_parse_minute_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("02:03.250"), 123.25)

    def test_parse_hour_timestamp(self) -> None:
        self.assertAlmostEqual(parse_time("01:02:03.5"), 3723.5)

    def test_format_time(self) -> None:
        self.assertEqual(format_time(123.25), "02:03.250")
        self.assertEqual(format_time(3723.5), "01:02:03.500")

    def test_even_dimensions(self) -> None:
        self.assertEqual(even_size(1081), 1080)
        self.assertEqual(even_size(1080), 1080)

    def test_center_crop_vertical(self) -> None:
        crop = compute_crop(self.video, "9:16")
        self.assertIsNotNone(crop)
        x, y, width, height = crop or (0, 0, 0, 0)
        self.assertEqual(height, 720)
        self.assertEqual(width % 2, 0)
        self.assertEqual(height % 2, 0)
        self.assertGreater(x, 0)
        self.assertEqual(y, 0)

    def test_custom_crop_rejects_outside_frame(self) -> None:
        with self.assertRaises(ValueError):
            compute_crop(self.video, "Custom", (1200, 0, 400, 400))

    def test_video_filters_include_speed_and_even_scale(self) -> None:
        filters = build_video_filters(
            self.video,
            "Original",
            None,
            "90°",
            1.5,
        )
        joined = ",".join(filters)
        self.assertIn("transpose=1", joined)
        self.assertIn("setpts=PTS/1.5", joined)
        self.assertIn("trunc(iw/2)*2", joined)

    def test_audio_filters(self) -> None:
        filters = build_audio_filters(1.25, 75, 1.0, 2.0, 10.0)
        joined = ",".join(filters)
        self.assertIn("atempo=1.25", joined)
        self.assertIn("volume=0.750", joined)
        self.assertIn("afade=t=in", joined)
        self.assertIn("afade=t=out", joined)

    def test_export_command_has_progress_and_output(self) -> None:
        output = Path("edited.mp4")
        command, duration = build_export_command(
            Path("input.mp4"),
            output,
            self.video,
            start=10.0,
            end=40.0,
            crop_preset="16:9",
            custom_crop=None,
            rotate="0°",
            speed=1.0,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        self.assertAlmostEqual(duration, 30.0)
        self.assertIn("-progress", command)
        self.assertEqual(command[-1], str(output))
        self.assertIn("-nostdin", command)

    def test_slow_motion_limits_input_before_decode(self) -> None:
        command, duration = build_export_command(
            Path("input.mp4"),
            Path("slow.mp4"),
            self.video,
            start=10.0,
            end=20.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=0.5,
            mute=False,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        self.assertAlmostEqual(duration, 20.0)
        self.assertLess(command.index("-t"), command.index("-i"))
        self.assertIn("setpts=PTS/0.5", ",".join(command))

    def test_audio_only_mute_exports_silence(self) -> None:
        audio = MediaInfo(
            duration=30.0,
            has_video=False,
            has_audio=True,
            width=0,
            height=0,
            fps=0.0,
        )
        command, _ = build_export_command(
            Path("input.mp3"),
            Path("muted.mp3"),
            audio,
            start=0.0,
            end=10.0,
            crop_preset="Original",
            custom_crop=None,
            rotate="0°",
            speed=1.0,
            mute=True,
            volume_percent=100,
            fade_in=0,
            fade_out=0,
            quality="High",
        )
        self.assertIn("-af", command)
        self.assertIn("volume=0.000", command[command.index("-af") + 1])

    @patch("media_editor_engine.subprocess.run")
    def test_probe_ignores_attached_cover_art(self, run_mock) -> None:
        run_mock.return_value.returncode = 1
        run_mock.return_value.stderr = """
Duration: 00:01:00.00, start: 0.000000, bitrate: 192 kb/s
Stream #0:0: Audio: mp3, 44100 Hz, stereo, fltp, 192 kb/s
Stream #0:1: Video: mjpeg, yuvj420p(pc), 600x600, 90k tbr, 90k tbn (attached pic)
"""
        info = probe_media(Path("song.mp3"))
        self.assertTrue(info.has_audio)
        self.assertFalse(info.has_video)
        self.assertEqual(info.width, 0)
        self.assertEqual(info.height, 0)

    def test_ratio_preset_accepts_moved_box(self) -> None:
        self.assertEqual(compute_crop(self.video, "9:16", (100, 0, 404, 720)), (100, 0, 404, 720))
        with self.assertRaises(ValueError):
            compute_crop(self.video, "9:16", (1000, 0, 404, 720))

    def test_new_ratio_presets(self) -> None:
        x, y, width, height = compute_crop(self.video, "4:3") or (0, 0, 0, 0)
        self.assertEqual(height, 720)
        self.assertEqual(width, 960)
        self.assertEqual(x, 160)

    def test_flip_rotate_color_order(self) -> None:
        filters = build_video_filters(self.video, "1:1", None, "90°", 1.0, flip_h=True, brightness=50,
                                      saturation=-100)
        names = [f.split("=")[0] for f in filters]
        self.assertLess(names.index("crop"), names.index("hflip"))
        self.assertLess(names.index("hflip"), names.index("transpose"))
        self.assertLess(names.index("transpose"), names.index("eq"))
        self.assertIn("saturation=0.000", filters[names.index("eq")])

    def test_color_filter_neutral_is_none(self) -> None:
        self.assertIsNone(color_filter(0, 0, 0))
        self.assertEqual(color_filter(0, 100, 0), "eq=contrast=2.000")

    def test_player_filters_skip_rotation(self) -> None:
        filters = build_player_filters(self.video, "Original", None, flip_v=True, contrast=20)
        self.assertEqual(filters, ["vflip", "eq=contrast=1.200"])

    def test_output_resolution_never_upscales(self) -> None:
        self.assertEqual(output_dimensions(self.video, None, "0°", "480p"), (852, 480))
        self.assertEqual(output_dimensions(self.video, None, "0°", "1080p"), (1280, 720))
        self.assertEqual(output_dimensions(self.video, (0, 0, 404, 720), "0°", "360p"), (360, 642))
        self.assertEqual(output_dimensions(self.video, None, "90°", "Original"), (720, 1280))
        filters = build_video_filters(self.video, "Original", None, "0°", 1.0, resolution="480p")
        self.assertEqual(filters[-1], "scale=852:480:flags=lanczos")

    def test_atempo_chain_covers_extreme_speeds(self) -> None:
        self.assertEqual(atempo_chain(1.0), [])
        self.assertEqual(atempo_chain(0.25), ["atempo=0.5", "atempo=0.5"])
        self.assertEqual(atempo_chain(4.0), ["atempo=2", "atempo=2"])
        self.assertEqual(atempo_chain(3.0), ["atempo=2", "atempo=1.5"])

    def test_safe_export_name(self) -> None:
        self.assertEqual(safe_export_name('bad:/name*?.mp4'), "bad_name_.mp4")


class SegmentExportTests(unittest.TestCase):
    INFO = MediaInfo(duration=10.0, has_video=True, has_audio=True, width=1280, height=720, fps=30.0)

    def command(self, pieces, info=None, **overrides):
        settings = dict(start=0.0, end=10.0, crop_preset="Original", custom_crop=None, rotate="0°", speed=1.0,
                        mute=False, volume_percent=100, fade_in=0, fade_out=0, quality="High")
        settings.update(overrides)
        return build_export_command(Path("in.mp4"), Path("out.mp4"), info or self.INFO, pieces=pieces, **settings)

    def test_single_keep_piece_uses_fast_trim(self) -> None:
        command, duration = self.command([(1.0, 4.0, "keep")])
        self.assertNotIn("-filter_complex", command)
        self.assertEqual(command[command.index("-ss") + 1], "1.000")
        self.assertAlmostEqual(duration, 3.0)

    def test_pieces_are_joined_with_concat(self) -> None:
        command, duration = self.command([(1.0, 2.0, "keep"), (4.0, 7.0, "keep"), (7.0, 8.0, "gap")], speed=2.0)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(command[command.index("-ss") + 1], "1.000")
        self.assertIn("trim=start=0.000:end=1.000", graph)
        self.assertIn("trim=start=3.000:end=6.000", graph)
        self.assertIn("drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill", graph)
        self.assertIn("volume=0", graph)
        self.assertIn("concat=n=3:v=1:a=1[vj][aj]", graph)
        self.assertIn("setpts=PTS/2", graph)
        self.assertIn("[vout]", command)
        self.assertAlmostEqual(duration, 2.5)

    def test_muted_video_drops_audio(self) -> None:
        command, _ = self.command([(0.0, 1.0, "keep"), (5.0, 6.0, "keep")], mute=True)
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("atrim", graph)
        self.assertIn("concat=n=2:v=1:a=0[vj]", graph)
        self.assertIn("-an", command)

    def test_audio_only_pieces(self) -> None:
        info = MediaInfo(duration=10.0, has_video=False, has_audio=True)
        command, _ = build_export_command(
            Path("in.mp3"), Path("out.mp3"), info, start=0, end=10, crop_preset="Original", custom_crop=None,
            rotate="0°", speed=1.0, mute=False, volume_percent=100, fade_in=1, fade_out=0, quality="High",
            pieces=[(0.0, 1.0, "keep"), (5.0, 6.0, "keep")],
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("[0:v:0]", graph)
        self.assertIn("concat=n=2:v=0:a=1[aj]", graph)
        self.assertIn("afade=t=in", graph)
        self.assertIn("libmp3lame", command)

    def test_empty_cut_list_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            self.command([])


if __name__ == "__main__":
    unittest.main()
