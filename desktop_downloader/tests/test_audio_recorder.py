import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audio_recorder_engine as audio  # noqa: E402
from media_editor_engine import ffmpeg_exe  # noqa: E402


def make_tone(path: Path, freq: int, seconds: float) -> None:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}:sample_rate=48000", "-ac", "2",
                    "-c:a", "pcm_s16le", str(path)], check=True)


def duration_of(path: Path) -> float:
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    from screen_recorder_engine import parse_duration

    return parse_duration(result.stderr) or 0.0


class CommandBuilderTests(unittest.TestCase):
    def test_mix_two_tracks_uses_amix_limiter_and_delay(self):
        command = audio.build_segment_mix_command(
            [audio.MixTrack(Path("a.wav")), audio.MixTrack(Path("b.wav"), offset=0.25, volume=1.5)],
            Path("out.wav"), 10.0)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("amix=inputs=2", graph)
        self.assertIn("alimiter", graph)
        self.assertIn("adelay=250:all=1", graph)
        self.assertIn("volume=1.500", graph)
        self.assertIn("apad=whole_dur=10.000", graph)
        self.assertEqual(command[command.index("-t") + 1], "10.000")

    def test_single_track_has_no_amix(self):
        command = audio.build_segment_mix_command([audio.MixTrack(Path("a.wav"))], Path("out.wav"), 5.0)
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("amix", graph)
        self.assertTrue(graph.endswith("[aout]"))

    def test_nothing_to_mix(self):
        with self.assertRaises(audio.AudioRecordingError):
            audio.build_segment_mix_command([], Path("out.wav"))

    def test_mp3_command(self):
        command = audio.build_mp3_command(Path("a.wav"), Path("a.mp3"), 256)
        self.assertIn("libmp3lame", command)
        self.assertIn("256k", command)

    def test_session_needs_a_source_and_a_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(audio.AudioRecordingError):
                audio.AudioSession(audio.AudioSettings(Path(tmp), system_audio=False, microphone=None))
            with self.assertRaises(audio.AudioRecordingError):
                audio.AudioSession(audio.AudioSettings(Path(tmp), save_wav=False, save_mp3=False))


class FakeRecorder:
    """Stands in for AudioRecorder: a WAV file already on disk."""

    def __init__(self, path: Path, *, loopback: bool, device=None, started_at=100.0, frames=48000):
        self.path, self.loopback, self.device = path, loopback, device
        self.started_at, self.frames, self.level, self.error = started_at, frames, 0.5, ""

    def start(self): pass
    def stop(self): pass
    def join(self, _timeout=None): pass
    def wait_started(self, _timeout=3.0): return True


class FinishTests(unittest.TestCase):
    def _run(self, **settings):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            seconds = {"system": 2.0, "mic": 2.0}

            def fake(path, *, loopback, device=None):
                kind = "system" if loopback else "mic"
                path.parent.mkdir(parents=True, exist_ok=True)
                make_tone(path, 440 if loopback else 880, seconds[kind])
                return FakeRecorder(path, loopback=loopback, device=device,
                                    started_at=100.0 + (0.0 if loopback else 0.2))

            with mock.patch.object(audio, "AudioRecorder", fake):
                session = audio.AudioSession(audio.AudioSettings(out, microphone="Mic", **settings))
                session.start()
                self.assertEqual(session.state, "recording")
                session.segments[-1].started = 100.0
                with mock.patch.object(audio.time, "monotonic", return_value=102.2):
                    session.pause()
                    self.assertEqual(session.state, "paused")
                files = session.stop()
                names = sorted(p.suffix for p in files)
                self.assertTrue(all(p.exists() for p in files))
                lengths = {p.suffix: duration_of(p) for p in files}
                self.assertFalse(session.work_dir.exists())
                return names, lengths

    def test_wav_and_mp3_are_both_written(self):
        names, lengths = self._run()
        self.assertEqual(names, [".mp3", ".wav"])
        self.assertAlmostEqual(lengths[".wav"], 2.2, delta=0.15)
        self.assertAlmostEqual(lengths[".mp3"], 2.2, delta=0.25)

    def test_mp3_only_leaves_no_wav(self):
        names, _lengths = self._run(save_wav=False)
        self.assertEqual(names, [".mp3"])


if __name__ == "__main__":
    unittest.main()
