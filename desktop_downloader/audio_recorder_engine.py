"""Audio-only recording: what the PC plays (the other side of a call) plus the microphone.

How it works
------------
* System sound (WASAPI loopback of an output device) and the microphone are captured by the
  same ``AudioRecorder`` the screen recorder uses, each into its own WAV file. Loopback hears
  everything the chosen speaker/headset plays, so a WhatsApp / Messenger call in a browser,
  Telegram, Zoom or Google Meet is recorded the same way: their voices come from the speaker,
  yours from the microphone.
* Pause closes the current segment, Resume opens a new one. Each segment lines its tracks up by
  the time their first sample arrived and mixes them into one stereo WAV.
* Stop joins the segments and writes the files the user asked for: WAV and/or MP3.

Nothing here touches Tk, so the command builders are unit tested on any platform.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_editor_engine import CREATE_NO_WINDOW, ffmpeg_exe
from screen_recorder_engine import (
    AUDIO_CHANNELS,
    AUDIO_RATE,
    AudioRecorder,
    unique_path,
)

MP3_BITRATES = {"128 kbps": 128, "192 kbps": 192, "256 kbps": 256, "320 kbps": 320}
MIN_SEGMENT = 0.05  # seconds; a shorter segment (a quick pause/resume) is dropped


@dataclass
class AudioSettings:
    output_dir: Path
    system_audio: bool = True
    speaker: str | None = None          # output device to capture, None = the default one
    microphone: str | None = None       # input device, None = off
    system_volume: float = 1.0
    mic_volume: float = 1.0
    save_wav: bool = True
    save_mp3: bool = True
    mp3_bitrate: int = 192
    file_stem: str = ""


@dataclass
class MixTrack:
    path: Path
    offset: float = 0.0     # seconds this track started after the segment's first sample
    volume: float = 1.0


class AudioRecordingError(RuntimeError):
    pass


def default_audio_stem(now: float | None = None) -> str:
    return time.strftime("Audio %Y-%m-%d %H-%M-%S", time.localtime(now))


# --------------------------------------------------------------------------- command builders
def build_segment_mix_command(tracks: list[MixTrack], output: Path, duration: float | None = None) -> list[str]:
    """Line the tracks up and mix them into one 48 kHz stereo WAV.

    ``duration`` pads the result with silence up to the real length of the segment and cuts it
    there, so a track that stopped early never shortens the recording.
    """
    if not tracks:
        raise AudioRecordingError("Nothing to mix.")
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for track in tracks:
        command += ["-i", str(track.path)]
    graph: list[str] = []
    labels: list[str] = []
    for index, track in enumerate(tracks):
        steps: list[str] = []
        if track.offset > 0.0005:
            steps.append(f"adelay={int(round(track.offset * 1000))}:all=1")
        if abs(track.volume - 1.0) > 0.001:
            steps.append(f"volume={track.volume:.3f}")
        steps.append(f"aresample={AUDIO_RATE}")
        graph.append(f"[{index}:a]{','.join(steps)}[a{index}]")
        labels.append(f"[a{index}]")
    pad = f",apad=whole_dur={duration:.3f}" if duration and duration > 0 else ""
    if len(tracks) > 1:
        # Two voices add up; the limiter keeps a loud moment from clipping.
        graph.append(f"{''.join(labels)}amix=inputs={len(tracks)}:duration=longest:normalize=0,"
                     f"alimiter=limit=0.97{pad}[aout]")
    else:
        graph[-1] = graph[-1].rsplit("[", 1)[0] + f"{pad}[aout]"
    limit = ["-t", f"{duration:.3f}"] if duration and duration > 0 else []
    return command + ["-filter_complex", ";".join(graph), "-map", "[aout]", "-ac", str(AUDIO_CHANNELS),
                      "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le", *limit, str(output)]


def concat_list_text(parts: list[Path]) -> str:
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def build_join_command(list_file: Path, output: Path) -> list[str]:
    return [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(output)]


def build_mp3_command(source: Path, output: Path, bitrate: int = 192) -> list[str]:
    return [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
            "-vn", "-c:a", "libmp3lame", "-b:a", f"{int(bitrate)}k", "-ar", "44100", str(output)]


def _run(command: list[str], what: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW, timeout=1800)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-3:]
        raise AudioRecordingError(f"{what} failed: {' '.join(detail) or 'ffmpeg error'}")


# --------------------------------------------------------------------------- session
@dataclass
class _Segment:
    recorders: list[tuple[AudioRecorder, float]]
    started: float
    ended: float | None = None


class AudioSession:
    """Start, pause, resume and stop one audio recording. Thread-safe for the UI thread."""

    def __init__(self, settings: AudioSettings, *, log: Callable[[str], None] | None = None) -> None:
        if not (settings.system_audio or settings.microphone):
            raise AudioRecordingError("Choose system sound, a microphone, or both.")
        if not (settings.save_wav or settings.save_mp3):
            raise AudioRecordingError("Choose WAV, MP3, or both.")
        self.settings = settings
        self.log = log or (lambda _message: None)
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        self.stem = settings.file_stem or default_audio_stem()
        self.work_dir = settings.output_dir / f".{self.stem}.parts"
        self.segments: list[_Segment] = []
        self.state = "idle"     # idle, recording, paused, finishing, done, failed
        self.error = ""
        self.warnings: list[str] = []
        self.outputs: list[Path] = []
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        with self._lock:
            if self.state not in {"idle", "paused"}:
                return
            self.work_dir.mkdir(parents=True, exist_ok=True)
            index = len(self.segments) + 1
            recorders: list[tuple[AudioRecorder, float]] = []
            if self.settings.system_audio:
                recorders.append((AudioRecorder(self.work_dir / f"seg{index:03d}_system.wav", loopback=True,
                                                device=self.settings.speaker), self.settings.system_volume))
            if self.settings.microphone:
                recorders.append((AudioRecorder(self.work_dir / f"seg{index:03d}_mic.wav", loopback=False,
                                                device=self.settings.microphone), self.settings.mic_volume))
            for recorder, _volume in recorders:
                recorder.start()
            started = time.monotonic()
            working = 0
            for recorder, _volume in recorders:
                if recorder.wait_started(4.0):
                    working += 1
                else:
                    name = "System sound" if recorder.loopback else "Microphone"
                    self.warnings.append(f"{name} could not be recorded: {recorder.error or 'no sound received'}")
            if working == 0:
                for recorder, _volume in recorders:
                    recorder.stop()
                self.state = "failed"
                self.error = "; ".join(self.warnings[-2:]) or "No sound source could be opened."
                raise AudioRecordingError(self.error)
            self.segments.append(_Segment(recorders, started))
            self.state = "recording"
            self.log(f"Audio segment {index} started")

    def pause(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            self._close_segment(self.segments[-1])
            self.state = "paused"

    def resume(self) -> None:
        self.start()

    def elapsed(self) -> float:
        with self._lock:
            total = 0.0
            for segment in self.segments:
                end = segment.ended if segment.ended is not None else time.monotonic()
                total += max(0.0, end - segment.started)
            return total

    def levels(self) -> tuple[float, float]:
        """Current peak of (system sound, microphone), 0..1."""
        system = mic = 0.0
        with self._lock:
            if self.state == "recording" and self.segments:
                for recorder, _volume in self.segments[-1].recorders:
                    if recorder.loopback:
                        system = recorder.level
                    else:
                        mic = recorder.level
        return system, mic

    def stop(self) -> list[Path]:
        """Stop and write the final files. Blocks: run it in a worker thread."""
        with self._lock:
            if self.state == "recording":
                self._close_segment(self.segments[-1])
            elif self.state != "paused":
                raise AudioRecordingError("Nothing is being recorded.")
            self.state = "finishing"
        try:
            self.outputs = self._finish()
            self.state = "done"
            return self.outputs
        except Exception as exc:
            self.state = "failed"
            self.error = str(exc)
            raise

    def abort(self) -> None:
        """Stop capturing without writing files (the window was closed mid-recording)."""
        with self._lock:
            if self.state == "recording":
                self._close_segment(self.segments[-1])
            self.state = "failed"
        shutil.rmtree(self.work_dir, ignore_errors=True)

    # ---------------------------------------------------------------- internals
    def _close_segment(self, segment: _Segment) -> None:
        segment.ended = time.monotonic()
        for recorder, _volume in segment.recorders:
            recorder.stop()
        for recorder, _volume in segment.recorders:
            recorder.join(3.0)

    def _segment_tracks(self, segment: _Segment) -> tuple[list[MixTrack], float]:
        live = [(recorder, volume) for recorder, volume in segment.recorders
                if recorder.started_at is not None and recorder.frames > 0 and recorder.path.exists()]
        if not live:
            return [], 0.0
        origin = min(recorder.started_at for recorder, _volume in live)  # type: ignore[type-var]
        tracks = [MixTrack(recorder.path, float(recorder.started_at - origin), volume)  # type: ignore[operator]
                  for recorder, volume in live]
        end = segment.ended if segment.ended is not None else time.monotonic()
        return tracks, max(0.0, end - origin)

    def _finish(self) -> list[Path]:
        mixed: list[Path] = []
        for index, segment in enumerate(self.segments, start=1):
            tracks, duration = self._segment_tracks(segment)
            if not tracks or duration < MIN_SEGMENT:
                continue
            target = self.work_dir / f"mix{index:03d}.wav"
            _run(build_segment_mix_command(tracks, target, duration), "Mixing the sound")
            mixed.append(target)
        if not mixed:
            raise AudioRecordingError("No sound was recorded.")

        if len(mixed) == 1:
            joined = mixed[0]
        else:
            list_file = self.work_dir / "parts.txt"
            list_file.write_text(concat_list_text(mixed), encoding="utf-8")
            joined = self.work_dir / "joined.wav"
            _run(build_join_command(list_file, joined), "Joining the parts")

        outputs: list[Path] = []
        directory = self.settings.output_dir
        if self.settings.save_wav:
            target = unique_path(directory / f"{self.stem}.wav")
            shutil.move(str(joined), str(target))
            outputs.append(target)
            mp3_source = target
        else:
            mp3_source = joined
        if self.settings.save_mp3:
            target = unique_path(directory / f"{self.stem}.mp3")
            _run(build_mp3_command(mp3_source, target, self.settings.mp3_bitrate), "Making the MP3")
            outputs.append(target)
        shutil.rmtree(self.work_dir, ignore_errors=True)
        return outputs
