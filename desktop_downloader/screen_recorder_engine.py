"""Screen recording engine: ffmpeg capture, audio capture, pause/resume and the final file.

How a recording is made
-----------------------
* The screen (and the webcam, if chosen) is captured by one ffmpeg process into a
  Matroska "part" file. Matroska survives a crash or power loss, unlike MP4.
* System sound (WASAPI loopback) and the microphone are captured in Python with the
  ``soundcard`` package into WAV files, because ffmpeg on Windows cannot record loopback
  audio. Each audio track remembers when its first sample arrived and the video start
  time is measured from ffmpeg's progress reports, so the tracks are lined up exactly
  when the file is finished.
* Pause stops the part; Resume starts a new one. Stop finishes the last part, then each
  part gets its audio (mixed into one track) and the parts are joined without
  re-encoding the video. The result is MP4 (or MKV).

Nothing here touches Tk, so the command builders are unit tested on any platform.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from media_editor_engine import CREATE_NO_WINDOW, ffmpeg_exe

try:  # optional: only used by the system sound / microphone capture
    import numpy  # type: ignore
except Exception:  # pragma: no cover - depends on the install
    numpy = None  # type: ignore

IS_WINDOWS = os.name == "nt"

QUALITIES = {"High": 18, "Balanced": 23, "Small": 28}
FRAME_RATES = [15, 24, 30, 60]
RESOLUTIONS = {"Original": None, "1080p": 1080, "720p": 720, "480p": 480}
ENCODERS = {
    "Auto": "auto",
    "CPU (x264)": "x264",
    "NVIDIA (NVENC)": "nvenc",
    "Intel (Quick Sync)": "qsv",
    "AMD (AMF)": "amf",
}
CONTAINERS = ["MP4", "MKV"]
WEBCAM_POSITIONS = ["Bottom right", "Bottom left", "Top right", "Top left"]
WEBCAM_SIZES = {"Small": 0.18, "Medium": 0.25, "Large": 0.33}
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
MIN_REGION = 64


# --------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    @property
    def label(self) -> str:
        primary = " (main)" if self.primary else ""
        return f"Screen {self.index + 1}{primary}: {self.width}×{self.height}"


@dataclass
class RecordSettings:
    region: tuple[int, int, int, int]                # x, y, w, h on the virtual desktop (physical px)
    output_dir: Path
    fps: int = 30
    quality: str = "High"
    resolution: str = "Original"
    encoder: str = "auto"                            # auto, x264, nvenc, qsv, amf
    container: str = "MP4"
    draw_mouse: bool = True
    system_audio: bool = True
    microphone: str | None = None                    # soundcard microphone name, None = off
    system_volume: float = 1.0
    mic_volume: float = 1.0
    webcam: str | None = None                        # DirectShow device name, None = off
    webcam_position: str = "Bottom right"
    webcam_size: str = "Small"
    capture_engine: str = "auto"                     # auto, gdigrab, ddagrab (Windows); x11grab elsewhere
    monitor_index: int | None = None                 # for ddagrab: the screen that holds the region
    monitor_origin: tuple[int, int] = (0, 0)
    file_stem: str = ""


# --------------------------------------------------------------------------- helpers
def even(value: int) -> int:
    value = max(2, int(value))
    return value - (value % 2)


def normalize_region(region: tuple[int, int, int, int], bounds: tuple[int, int, int, int] | None = None
                     ) -> tuple[int, int, int, int]:
    """Clamp a capture rectangle into ``bounds`` and make its size even (needed by H.264)."""
    x, y, w, h = (int(round(value)) for value in region)
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    if bounds is not None:
        bx, by, bw, bh = bounds
        x = max(bx, min(x, bx + bw - MIN_REGION))
        y = max(by, min(y, by + bh - MIN_REGION))
        w = min(w, bx + bw - x)
        h = min(h, by + bh - y)
    w = even(max(MIN_REGION, w))
    h = even(max(MIN_REGION, h))
    return x, y, w, h


def output_size(region: tuple[int, int, int, int], resolution: str) -> tuple[int, int]:
    _x, _y, w, h = region
    target = RESOLUTIONS.get(resolution)
    if not target or h <= target:
        return even(w), even(h)
    scale = target / h
    return even(round(w * scale)), even(target)


def default_file_stem(now: float | None = None) -> str:
    return time.strftime("Recording %Y-%m-%d %H-%M-%S", time.localtime(now))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def capture_start_from_log(line: str, wall_now: float, mono_now: float) -> float | None:
    """The first captured frame's time on the monotonic clock, from ffmpeg's input line
    ``Duration: N/A, start: 1794949616.662669, bitrate: ...``.

    gdigrab and x11grab stamp frames with the wall clock (or, in some builds, a monotonic
    clock); whichever clock the value is close to is the one it came from.
    """
    match = re.search(r"start:\s*(-?[\d.]+)", line)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if abs(value - wall_now) < 120:
        return mono_now - (wall_now - value)
    if abs(value - mono_now) < 120:
        return value
    return None


def _run(command: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace", creationflags=CREATE_NO_WINDOW, timeout=timeout)


# --------------------------------------------------------------------------- screens
def list_monitors() -> list[Monitor]:
    """Screens in physical pixels (the app is per-monitor DPI aware). Empty off Windows."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    found: list[tuple[int, int, int, int, bool]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def collect(hmonitor: Any, _hdc: Any, _rect: Any, _data: Any) -> bool:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            found.append((r.left, r.top, r.right - r.left, r.bottom - r.top, bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
    # Desktop Duplication numbers outputs in the order Windows enumerates them.
    return [Monitor(index, *values) for index, values in enumerate(found)]


def virtual_bounds(monitors: list[Monitor]) -> tuple[int, int, int, int] | None:
    if not monitors:
        return None
    left = min(m.left for m in monitors)
    top = min(m.top for m in monitors)
    right = max(m.left + m.width for m in monitors)
    bottom = max(m.top + m.height for m in monitors)
    return left, top, right - left, bottom - top


def monitor_for_region(monitors: list[Monitor], region: tuple[int, int, int, int]) -> Monitor | None:
    """The screen that fully contains ``region``, if any (Desktop Duplication needs one)."""
    x, y, w, h = region
    for monitor in monitors:
        if (monitor.left <= x and monitor.top <= y and x + w <= monitor.left + monitor.width
                and y + h <= monitor.top + monitor.height):
            return monitor
    return None


# --------------------------------------------------------------------------- capabilities
_capability_cache: dict[str, Any] = {}
_capability_lock = threading.Lock()


def _try_encode(args: list[str], source: str = "color=c=black:s=320x240:r=30:d=0.2") -> bool:
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i", source,
               *args, "-f", "null", "-"]
    try:
        return _run(command, timeout=20).returncode == 0
    except Exception:
        return False


def available_encoders() -> list[str]:
    """Hardware H.264 encoders that really work on this PC, plus x264. Slow: call in a thread."""
    with _capability_lock:
        if "encoders" in _capability_cache:
            return list(_capability_cache["encoders"])
    usable = ["x264"]
    try:
        listing = _run([ffmpeg_exe(), "-hide_banner", "-encoders"], timeout=15).stdout
    except Exception:
        listing = ""
    for key, codec in (("nvenc", "h264_nvenc"), ("qsv", "h264_qsv"), ("amf", "h264_amf")):
        if codec in listing and _try_encode(video_codec_args(key, "Balanced", 30)):
            usable.append(key)
    with _capability_lock:
        _capability_cache["encoders"] = usable
    return list(usable)


def ddagrab_available() -> bool:
    """Desktop Duplication capture (GPU, low CPU, captures full-screen games)."""
    if not IS_WINDOWS:
        return False
    with _capability_lock:
        if "ddagrab" in _capability_cache:
            return bool(_capability_cache["ddagrab"])
    ok = False
    try:
        listing = _run([ffmpeg_exe(), "-hide_banner", "-filters"], timeout=15).stdout
        if "ddagrab" in listing:
            command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                       "ddagrab=framerate=5", "-vf", "hwdownload,format=bgra", "-frames:v", "2", "-f", "null", "-"]
            ok = _run(command, timeout=20).returncode == 0
    except Exception:
        ok = False
    with _capability_lock:
        _capability_cache["ddagrab"] = ok
    return ok


_DSHOW_LINE = re.compile(r'"(?P<name>[^"]+)"\s*\((?P<kind>video|audio|none)\)')


def parse_dshow_devices(text: str) -> tuple[list[str], list[str]]:
    """Device names from ``ffmpeg -list_devices true -f dshow -i dummy`` (old and new formats)."""
    videos: list[str] = []
    audios: list[str] = []
    section = ""
    for line in text.splitlines():
        if "Alternative name" in line:
            continue
        if "DirectShow video devices" in line:
            section = "video"
            continue
        if "DirectShow audio devices" in line:
            section = "audio"
            continue
        match = _DSHOW_LINE.search(line)
        if match:
            kind = match.group("kind")
            target = videos if kind == "video" else audios if kind == "audio" else None
            if target is not None and match.group("name") not in target:
                target.append(match.group("name"))
            continue
        quoted = re.search(r'\]\s+"([^"]+)"\s*$', line)
        if quoted and section:
            target = videos if section == "video" else audios
            if quoted.group(1) not in target:
                target.append(quoted.group(1))
    return videos, audios


def list_webcams() -> list[str]:
    if not IS_WINDOWS:
        return []
    try:
        result = _run([ffmpeg_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                      timeout=15)
    except Exception:
        return []
    videos, _audios = parse_dshow_devices(result.stderr)
    return videos


# --------------------------------------------------------------------------- audio devices
def _com_init() -> Callable[[], None]:
    """soundcard uses COM; worker threads need it initialised first."""
    if not IS_WINDOWS:
        return lambda: None
    import ctypes

    try:
        result = ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    except Exception:
        return lambda: None
    if result in (0, 1):  # S_OK, S_FALSE
        return lambda: ctypes.windll.ole32.CoUninitialize()
    return lambda: None


def audio_backend_error() -> str:
    """Empty when system sound / microphone capture can work, else the reason."""
    if numpy is None:
        return "The numpy package is missing."
    try:
        import soundcard  # noqa: F401  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on the install
        return f"The soundcard package is not available ({exc})."
    return ""


def list_microphones() -> list[str]:
    """Input devices (microphones), default first. Call from a worker thread."""
    if audio_backend_error():
        return []
    done = _com_init()
    try:
        import soundcard as sc  # type: ignore

        names: list[str] = []
        try:
            default = sc.default_microphone()
            names.append(default.name)
        except Exception:
            pass
        for mic in sc.all_microphones(include_loopback=False):
            if mic.name not in names:
                names.append(mic.name)
        return names
    except Exception:
        return []
    finally:
        done()


# --------------------------------------------------------------------------- command builders
def video_codec_args(encoder: str, quality: str, fps: int, *, heavy: bool = False) -> list[str]:
    crf = QUALITIES.get(quality, 18)
    gop = str(max(1, int(fps)) * 2)  # a keyframe every 2 s: fast seeking in the editor
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "0",
                "-g", gop, "-pix_fmt", "yuv420p"]
    if encoder == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", str(crf + 2), "-g", gop,
                "-pix_fmt", "nv12"]
    if encoder == "amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf + 2),
                "-g", gop, "-pix_fmt", "yuv420p"]
    preset = "ultrafast" if heavy else "veryfast"
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", gop, "-pix_fmt", "yuv420p"]


def resolve_encoder(requested: str, usable: list[str]) -> str:
    if requested in usable:
        return requested
    if requested == "auto":
        for candidate in ("nvenc", "qsv", "amf"):
            if candidate in usable:
                return candidate
    return "x264"


def _webcam_overlay(settings: RecordSettings, out_w: int, out_h: int) -> tuple[int, str]:
    fraction = WEBCAM_SIZES.get(settings.webcam_size, 0.18)
    cam_w = even(round(out_w * fraction))
    margin = max(8, even(round(min(out_w, out_h) * 0.02)))
    position = settings.webcam_position
    x = f"{margin}" if "left" in position else f"W-w-{margin}"
    y = f"{margin}" if "Top" in position else f"H-h-{margin}"
    return cam_w, f"{x}:{y}"


def build_capture_command(settings: RecordSettings, output: Path, *, engine: str, encoder: str) -> list[str]:
    """One ffmpeg process: screen (+ webcam) to a Matroska part, no audio."""
    region = settings.region
    x, y, w, h = region
    out_w, out_h = output_size(region, settings.resolution)
    fps = max(1, int(settings.fps))
    heavy = fps >= 60 or w * h > 1920 * 1080

    # No -nostdin: Stop sends "q" on stdin so ffmpeg closes the file properly. "info" level
    # makes ffmpeg print the capture's first frame time ("start: ..."), used for A/V sync.
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y"]
    pre_filters: list[str] = []
    if engine == "ddagrab":
        ox, oy = x - settings.monitor_origin[0], y - settings.monitor_origin[1]
        source = (f"ddagrab=output_idx={settings.monitor_index or 0}:framerate={fps}"
                  f":draw_mouse={1 if settings.draw_mouse else 0}:offset_x={ox}:offset_y={oy}:video_size={w}x{h}")
        command += ["-f", "lavfi", "-i", source]
        pre_filters.append("hwdownload,format=bgra")
    elif engine == "x11grab":  # development and tests on Linux
        display = os.environ.get("DISPLAY", ":0")
        command += ["-f", "x11grab", "-thread_queue_size", "1024", "-framerate", str(fps),
                    "-draw_mouse", "1" if settings.draw_mouse else "0", "-video_size", f"{w}x{h}",
                    "-i", f"{display}+{x},{y}"]
    else:
        command += ["-f", "gdigrab", "-thread_queue_size", "1024", "-framerate", str(fps),
                    "-draw_mouse", "1" if settings.draw_mouse else "0", "-offset_x", str(x), "-offset_y", str(y),
                    "-video_size", f"{w}x{h}", "-i", "desktop"]

    if settings.webcam:
        command += ["-f", "dshow", "-thread_queue_size", "512", "-rtbufsize", "128M", "-i",
                    f"video={settings.webcam}"]

    chain = list(pre_filters)
    if (out_w, out_h) != (w, h):
        chain.append(f"scale={out_w}:{out_h}:flags=bicubic")
    pixel_format = "nv12" if encoder == "qsv" else "yuv420p"
    graph: list[str] = []
    if settings.webcam:
        cam_w, position = _webcam_overlay(settings, out_w, out_h)
        graph.append(f"[0:v]{','.join(chain) or 'null'}[screen]")
        graph.append(f"[1:v]scale={cam_w}:-2[cam]")
        graph.append(f"[screen][cam]overlay={position}:eof_action=pass:shortest=0,format={pixel_format}[vout]")
    else:
        graph.append(f"[0:v]{','.join([*chain, f'format={pixel_format}'])}[vout]")

    command += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-an",
                *video_codec_args(encoder, settings.quality, fps, heavy=heavy),
                "-r", str(fps), "-progress", "pipe:1", "-nostats", "-f", "matroska", str(output)]
    return command


@dataclass
class AudioTrack:
    path: Path
    offset: float = 0.0   # seconds; > 0 means the audio started after the first video frame
    volume: float = 1.0


def build_part_mux_command(video: Path, tracks: list[AudioTrack], output: Path,
                           duration: float | None = None) -> list[str]:
    """Add the aligned, mixed audio to one video part. The video is copied, not re-encoded.

    ``duration`` is the length of the part: the audio is padded with silence up to it and
    the output is cut there. It also trims the part where the user pressed Pause/Stop
    (ffmpeg only notices the "q" a few hundred milliseconds later).

    Never pad without a duration: ``apad`` makes an endless stream, and with a copied video
    stream the ffmpeg build the app ships (7.x) ignores ``-shortest`` and runs forever. That
    was the "recording never finishes / app hangs after Stop" bug.
    """
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video)]
    for track in tracks:
        command += ["-i", str(track.path)]
    limit = ["-t", f"{duration:.3f}"] if duration and duration > 0 else []
    if not tracks:
        return command + ["-map", "0:v:0", "-c", "copy", *limit, "-f", "matroska", str(output)]

    graph: list[str] = []
    labels: list[str] = []
    for index, track in enumerate(tracks, start=1):
        steps = []
        if track.offset > 0.0005:
            delay = int(round(track.offset * 1000))
            steps.append(f"adelay={delay}:all=1")
        elif track.offset < -0.0005:
            steps.append(f"atrim=start={-track.offset:.3f},asetpts=PTS-STARTPTS")
        if abs(track.volume - 1.0) > 0.001:
            steps.append(f"volume={track.volume:.3f}")
        steps.append(f"aresample={AUDIO_RATE}")
        graph.append(f"[{index}:a]{','.join(steps)}[a{index}]")
        labels.append(f"[a{index}]")
    # With a known length the audio is padded with silence up to the end of the video, so
    # a track that stopped a moment early or started late never shortens the video.
    pad = f",apad=whole_dur={duration:.3f}" if duration and duration > 0 else ""
    if len(tracks) > 1:
        graph.append(f"{''.join(labels)}amix=inputs={len(tracks)}:duration=longest:normalize=0{pad}[aout]")
    else:
        graph[-1] = graph[-1].rsplit("[", 1)[0].rstrip() + f"{pad}[aout]"
    return command + ["-filter_complex", ";".join(graph), "-map", "0:v:0", "-map", "[aout]",
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", *limit, "-f", "matroska",
                      str(output)]


_DURATION_LINE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_TIME_STAT = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _hms(match: re.Match[str]) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_duration(text: str) -> float | None:
    """Length from ffmpeg's ``Duration: 00:01:02.50`` header line (None for N/A)."""
    match = _DURATION_LINE.search(text or "")
    if not match:
        return None
    value = _hms(match)
    return value if value > 0 else None


def parse_last_time(text: str) -> float | None:
    """The last ``time=00:00:05.00`` progress value ffmpeg printed."""
    found = list(_TIME_STAT.finditer(text or ""))
    if not found:
        return None
    value = _hms(found[-1])
    return value if value > 0 else None


def probe_duration(path: Path) -> float | None:
    """Length of a recorded part. Reads the header, or (for a file cut off by a crash,
    which has no length in its header) reads the whole video stream without decoding it."""
    try:
        result = _run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)], timeout=30)
        value = parse_duration(result.stderr)
        if value:
            return value
        result = _run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                       "-f", "null", "-"], timeout=300)
        return parse_last_time(result.stderr)
    except Exception:
        return None


def part_length(probed: float | None, wanted: float | None) -> float | None:
    """The length to give a finished part: where the user stopped, never past the video."""
    values = [value for value in (probed, wanted) if value and value > 0]
    return min(values) if values else None


def build_join_command(parts_list: Path, output: Path) -> list[str]:
    """Join finished parts (concat demuxer, stream copy) into the final file."""
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
               "-i", str(parts_list), "-map", "0", "-c", "copy"]
    if output.suffix.lower() in {".mp4", ".mov"}:
        command += ["-movflags", "+faststart"]
    return command + [str(output)]


def concat_list_text(parts: list[Path]) -> str:
    lines = []
    for part in parts:
        escaped = str(part).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- audio capture
class AudioRecorder(threading.Thread):
    """Records one soundcard source (system sound loopback or a microphone) to a WAV file.

    ``started_at`` is the monotonic time of the first sample. When the device delivers less
    audio than wall-clock time (a driver that stops sending data in silence), silence is
    inserted so the track never drifts away from the video.
    """

    def __init__(self, path: Path, *, loopback: bool, device: str | None = None) -> None:
        super().__init__(name=f"audio-{'loopback' if loopback else 'mic'}", daemon=True)
        self.path = path
        self.loopback = loopback
        self.device = device
        self.started_at: float | None = None
        self.error = ""
        self.frames = 0
        self.level = 0.0
        self._stop_event = threading.Event()
        self._ready = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def wait_started(self, timeout: float = 3.0) -> bool:
        self._ready.wait(timeout)
        return not self.error

    def _open_source(self) -> Any:
        import soundcard as sc  # type: ignore

        if self.loopback:
            speaker = sc.default_speaker()
            return sc.get_microphone(id=str(speaker.name), include_loopback=True)
        if self.device:
            for mic in sc.all_microphones(include_loopback=False):
                if mic.name == self.device:
                    return mic
        return sc.default_microphone()

    def run(self) -> None:
        done = _com_init()
        try:
            source = self._open_source()
            block = AUDIO_RATE // 50  # 20 ms
            with wave.open(str(self.path), "wb") as wav, source.recorder(samplerate=AUDIO_RATE,
                                                                          channels=AUDIO_CHANNELS) as recorder:
                wav.setnchannels(AUDIO_CHANNELS)
                wav.setsampwidth(2)
                wav.setframerate(AUDIO_RATE)
                first = True
                while not self._stop_event.is_set():
                    data = recorder.record(numframes=block)
                    now = time.monotonic()
                    if data is None or len(data) == 0:
                        continue
                    if data.ndim == 1:
                        data = data.reshape(-1, 1)
                    if data.shape[1] < AUDIO_CHANNELS:
                        data = numpy.repeat(data[:, :1], AUDIO_CHANNELS, axis=1)
                    elif data.shape[1] > AUDIO_CHANNELS:
                        data = data[:, :AUDIO_CHANNELS]
                    if first:
                        self.started_at = now - len(data) / AUDIO_RATE
                        first = False
                        self._ready.set()
                    else:
                        expected = int((now - len(data) / AUDIO_RATE - self.started_at) * AUDIO_RATE)
                        missing = expected - self.frames
                        if missing > AUDIO_RATE // 7:  # more than ~140 ms behind: fill with silence
                            wav.writeframes(bytes(missing * AUDIO_CHANNELS * 2))
                            self.frames += missing
                    pcm = (numpy.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
                    self.level = float(numpy.abs(data).max()) if data.size else 0.0
                    wav.writeframes(pcm.tobytes())
                    self.frames += len(data)
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
        finally:
            self._ready.set()
            done()


class LevelMonitor(threading.Thread):
    """Live input level of system sound or a microphone, before recording starts.

    Nothing is written anywhere: it only keeps ``level`` (peak, 0..1) up to date so the
    recorder can show a moving meter, the way Bandicam does, and the user can see that the
    microphone really hears them.
    """

    def __init__(self, *, loopback: bool, device: str | None = None) -> None:
        super().__init__(name=f"level-{'loopback' if loopback else 'mic'}", daemon=True)
        self.loopback = loopback
        self.device = device
        self.level = 0.0
        self.error = ""
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def run(self) -> None:
        if audio_backend_error():
            self.error = audio_backend_error()
            return
        done = _com_init()
        try:
            source = AudioRecorder(Path(os.devnull), loopback=self.loopback, device=self.device)._open_source()
            with source.recorder(samplerate=AUDIO_RATE, channels=AUDIO_CHANNELS) as recorder:
                while not self._stop_event.is_set():
                    data = recorder.record(numframes=AUDIO_RATE // 25)  # 40 ms
                    if data is None or len(data) == 0:
                        self.level = 0.0
                        continue
                    self.level = float(numpy.abs(data).max())
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
            self.level = 0.0
        finally:
            done()


def level_fraction(level: float, floor_db: float = -60.0) -> float:
    """Peak level (0..1) to a meter fill (0..1) on a dB scale, like a mixer meter."""
    import math

    if level <= 0.0:
        return 0.0
    db = 20.0 * math.log10(min(1.0, level))
    return max(0.0, min(1.0, (db - floor_db) / -floor_db))


# --------------------------------------------------------------------------- session
@dataclass
class _Part:
    video: Path
    process: subprocess.Popen[str] | None = None
    audio: list[tuple[AudioRecorder, float]] = field(default_factory=list)  # recorder, volume
    launched_at: float = 0.0
    video_started_at: float | None = None
    ended_at: float | None = None
    quit_sent_at: float | None = None
    capture_start: float | None = None   # first frame, monotonic clock, from ffmpeg's "start:"
    stderr_lines: list[str] = field(default_factory=list)
    out_time: float = 0.0
    total_size: int = 0
    dropped_frames: int = 0


class RecordingError(RuntimeError):
    pass


class RecordingSession:
    """Start, pause, resume and stop one recording. Thread-safe for the UI thread."""

    def __init__(self, settings: RecordSettings, *, usable_encoders: list[str] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.settings = settings
        self.log = log or (lambda _message: None)
        self.encoder = resolve_encoder(settings.encoder, usable_encoders or ["x264"])
        self.engine = self._pick_engine()
        stem = settings.file_stem or default_file_stem()
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".mkv" if settings.container.upper() == "MKV" else ".mp4"
        self.output = unique_path(settings.output_dir / f"{stem}{suffix}")
        self.work_dir = settings.output_dir / f".{self.output.stem}.parts"
        self.parts: list[_Part] = []
        self.state = "idle"     # idle, recording, paused, finishing, done, failed
        self.error = ""
        self.warnings: list[str] = []
        self._lock = threading.RLock()
        self._recorded_before = 0.0

    # ---------------------------------------------------------------- engine choice
    def _pick_engine(self) -> str:
        requested = self.settings.capture_engine
        if not IS_WINDOWS:
            return "x11grab"
        if requested == "gdigrab":
            return "gdigrab"
        if self.settings.monitor_index is None or not ddagrab_available():
            return "gdigrab"
        if requested == "ddagrab":
            return "ddagrab"
        # Auto: Desktop Duplication numbers screens its own way, so it is only picked by
        # itself on a single-screen PC, where the screen cannot be mixed up.
        if requested == "auto" and len(list_monitors()) == 1:
            return "ddagrab"
        return "gdigrab"

    # ---------------------------------------------------------------- status
    @property
    def elapsed(self) -> float:
        with self._lock:
            total = self._recorded_before
            if self.state == "recording" and self.parts:
                part = self.parts[-1]
                start = part.capture_start or part.video_started_at or part.launched_at
                total += max(0.0, time.monotonic() - start)
            return total

    @property
    def size(self) -> int:
        total = 0
        for part in self.parts:
            try:
                total += max(part.video.stat().st_size, part.total_size)
            except OSError:
                total += part.total_size
            for recorder, _volume in part.audio:
                try:
                    total += recorder.path.stat().st_size
                except OSError:
                    pass
        return total

    def audio_levels(self) -> dict[str, float]:
        levels: dict[str, float] = {}
        if self.parts and self.state == "recording":
            for recorder, _volume in self.parts[-1].audio:
                levels["system" if recorder.loopback else "mic"] = recorder.level
        return levels

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        with self._lock:
            if self.state not in {"idle", "paused"}:
                return
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self._hide_dir(self.work_dir)
            self._free_space_check()
            self._start_part()
            self.state = "recording"

    def pause(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            part = self.parts[-1]
            self._stop_part(part)
            self._recorded_before += self._part_duration(part)
            self.state = "paused"
            self._check_part(part)

    def resume(self) -> None:
        self.start()

    def stop(self) -> Path:
        """Stop and build the final file. Blocks: run it in a worker thread."""
        with self._lock:
            if self.state == "recording":
                part = self.parts[-1]
                self._stop_part(part)
                self._recorded_before += self._part_duration(part)
                self._check_part(part)
            elif self.state not in {"paused"}:
                raise RecordingError("Nothing is being recorded.")
            self.state = "finishing"
        try:
            output = self._finish()
            self.state = "done"
            return output
        except Exception as exc:
            self.state = "failed"
            self.error = str(exc)
            raise

    def abort(self) -> None:
        """Stop capturing without building the final file (the app is quitting).

        The parts are closed properly and kept, so the recorder offers to save them the
        next time it opens.
        """
        with self._lock:
            for part in self.parts:
                self._stop_part(part, graceful=True)
            self.state = "failed"

    # ---------------------------------------------------------------- parts
    def _start_part(self) -> None:
        index = len(self.parts) + 1
        video = self.work_dir / f"part{index:03d}.mkv"
        part = _Part(video=video)
        command = build_capture_command(self.settings, video, engine=self.engine, encoder=self.encoder)
        self.log(f"Recording part {index}: {' '.join(command)}")

        recorders: list[tuple[AudioRecorder, float]] = []
        if self.settings.system_audio:
            recorders.append((AudioRecorder(self.work_dir / f"part{index:03d}_system.wav", loopback=True),
                              self.settings.system_volume))
        if self.settings.microphone:
            recorders.append((AudioRecorder(self.work_dir / f"part{index:03d}_mic.wav", loopback=False,
                                            device=self.settings.microphone), self.settings.mic_volume))
        for recorder, _volume in recorders:
            recorder.start()

        part.launched_at = time.monotonic()
        part.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._read_progress, args=(part,), daemon=True).start()
        threading.Thread(target=self._read_errors, args=(part,), daemon=True).start()

        # A failed start (bad capture device, encoder or region) shows up within a moment.
        deadline = time.monotonic() + 4.0
        while (time.monotonic() < deadline and part.capture_start is None and part.video_started_at is None
               and part.process.poll() is None):
            time.sleep(0.03)
        if part.process.poll() is not None:
            for recorder, _volume in recorders:
                recorder.stop()
            detail = "\n".join(part.stderr_lines[-8:]).strip()
            if self.engine == "ddagrab":
                self.warnings.append("Desktop Duplication capture failed; using GDI capture instead.")
                self.log(f"ddagrab failed, falling back to gdigrab: {detail}")
                self.engine = "gdigrab"
                return self._start_part()
            if self.settings.webcam:
                self.warnings.append(f"The webcam could not be opened (in use by another app?); "
                                     f"recording without it.")
                self.log(f"Webcam failed, recording without it: {detail}")
                self.settings.webcam = None
                return self._start_part()
            if self.encoder != "x264":  # a GPU encoder that is busy or unsupported
                self.warnings.append(f"The {self.encoder} encoder failed; using the CPU encoder instead.")
                self.log(f"{self.encoder} failed, falling back to x264: {detail}")
                self.encoder = "x264"
                return self._start_part()
            raise RecordingError(detail or "ffmpeg could not start the screen capture.")

        for recorder, volume in recorders:
            if recorder.wait_started(3.0):
                part.audio.append((recorder, volume))
            else:
                recorder.stop()
                kind = "System sound" if recorder.loopback else "Microphone"
                self.warnings.append(f"{kind} could not be recorded: {recorder.error}")
                self.log(f"{kind} capture failed: {recorder.error}")
        self.parts.append(part)

    def _read_progress(self, part: _Part) -> None:
        process = part.process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            key, _, value = raw.strip().partition("=")
            now = time.monotonic()
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                if seconds <= 0:
                    continue
                part.out_time = seconds
                # The first frame was captured "seconds" of video before this report. The
                # smallest estimate is the most accurate (reports only ever arrive late).
                estimate = now - seconds
                if part.video_started_at is None or estimate < part.video_started_at:
                    part.video_started_at = estimate
            elif key == "total_size":
                try:
                    part.total_size = int(value)
                except ValueError:
                    pass
            elif key == "drop_frames":
                try:
                    part.dropped_frames = int(value)
                except ValueError:
                    pass

    def _read_errors(self, part: _Part) -> None:
        process = part.process
        if process is None or process.stderr is None:
            return
        for raw in process.stderr:
            line = raw.strip()
            if not line:
                continue
            if part.capture_start is None and "start:" in line and "Duration:" in line:
                part.capture_start = capture_start_from_log(line, time.time(), time.monotonic())
                continue
            if line.startswith(("[libx264", "Stream", "Input #", "Output #", "Press [q]", "Metadata",
                                "encoder", "Side data", "cpb:")):
                continue
            part.stderr_lines.append(line)
            del part.stderr_lines[:-40]

    def _stop_part(self, part: _Part, *, graceful: bool = True) -> None:
        process = part.process
        part.ended_at = time.monotonic()
        for recorder, _volume in part.audio:
            recorder.stop()
        if process is not None and process.poll() is None:
            try:
                if graceful and process.stdin is not None:
                    part.quit_sent_at = time.monotonic()
                    process.stdin.write("q")
                    process.stdin.flush()
                    process.stdin.close()
                    process.wait(timeout=90)  # the encoder may still be catching up
                else:
                    process.kill()
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
        for recorder, _volume in part.audio:
            recorder.join(timeout=3)

    def _video_start(self, part: _Part) -> float:
        """When the first frame of ``part`` was captured, on the monotonic clock.

        Best: the capture's own time stamp of the first frame, which ffmpeg prints. The
        fallback, from the progress reports, is late by the encoder's delay (x264 holds back
        up to a second of frames), so it is corrected by the delay seen at the end.
        """
        if part.capture_start is not None:
            return part.capture_start
        estimate = part.video_started_at or part.launched_at
        self.log("No capture time stamp; audio sync uses the progress estimate")
        return estimate

    def _part_duration(self, part: _Part) -> float:
        start = part.capture_start or part.video_started_at or part.launched_at
        end = part.ended_at or time.monotonic()
        return max(0.0, end - start)

    def _check_part(self, part: _Part) -> None:
        if part.process is not None and part.process.returncode not in (0, None, 255):
            detail = "\n".join(part.stderr_lines[-6:])
            self.warnings.append(f"Capture ended with an error: {detail[-300:]}")
        frames = part.out_time * max(1, self.settings.fps)
        if frames > 0 and part.dropped_frames > max(10, frames * 0.02):
            self.warnings.append(
                f"{part.dropped_frames} frames were dropped: the PC could not keep up. Try a lower frame "
                "rate or size, a GPU encoder, or the Desktop Duplication capture.")

    # ---------------------------------------------------------------- finishing
    def _finish(self) -> Path:
        finished: list[Path] = []
        for index, part in enumerate(self.parts, start=1):
            if not part.video.exists() or part.video.stat().st_size == 0:
                self.log(f"Part {index} has no video, skipped")
                continue
            tracks: list[AudioTrack] = []
            video_start = self._video_start(part) if part.audio else 0.0
            for recorder, volume in part.audio:
                if recorder.error or recorder.started_at is None or not recorder.path.exists():
                    continue
                self.log(f"Part {index} {'loopback' if recorder.loopback else 'mic'} audio offset "
                         f"{recorder.started_at - video_start:+.3f}s")
                tracks.append(AudioTrack(recorder.path, offset=recorder.started_at - video_start, volume=volume))
            muxed = self.work_dir / f"part{index:03d}_final.mkv"
            wanted = None
            if part.capture_start is not None and part.quit_sent_at is not None:
                wanted = part.quit_sent_at - part.capture_start
            length = part_length(probe_duration(part.video), wanted)
            if length is None and part.out_time > 0:
                length = part.out_time
            self.log(f"Part {index} length {length if length is not None else 'unknown'}")
            if not self._mux_part(part.video, tracks, muxed, length):
                if tracks:
                    self.warnings.append(f"Audio of part {index} could not be added.")
                muxed = part.video
            finished.append(muxed)

        if not finished:
            raise RecordingError("The recording is empty. ffmpeg did not capture any frames.\n\n"
                                 + "\n".join(self.parts[-1].stderr_lines[-6:] if self.parts else []))

        parts_list = self.work_dir / "parts.txt"
        parts_list.write_text(concat_list_text(finished), encoding="utf-8")
        try:
            result = _run(build_join_command(parts_list, self.output), timeout=1800)
        except subprocess.TimeoutExpired:
            raise RecordingError("Joining the recording took too long. The raw recording is kept in:\n"
                                 f"{self.work_dir}") from None
        if result.returncode != 0 or not self.output.exists():
            # Keep the raw parts so nothing is lost, and say where they are.
            raise RecordingError("Could not write the final file. The raw recording is kept in:\n"
                                 f"{self.work_dir}\n\n{result.stderr[-800:]}")
        shutil.rmtree(self.work_dir, ignore_errors=True)
        return self.output

    def _mux_part(self, video: Path, tracks: list[AudioTrack], output: Path, length: float | None) -> bool:
        """Put the audio on one part. Never blocks for long: a stuck ffmpeg is killed."""
        timeout = max(120.0, (length or 600.0) * 2)
        try:
            result = _run(build_part_mux_command(video, tracks, output, length), timeout=timeout)
        except subprocess.TimeoutExpired:
            self.log(f"Mux of {video.name} timed out after {timeout:.0f}s")
            return False
        except Exception as exc:
            self.log(f"Mux of {video.name} failed: {exc}")
            return False
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            self.log(f"Mux of {video.name} failed: {result.stderr[-800:]}")
            return False
        return True

    # ---------------------------------------------------------------- misc
    def free_space(self) -> int | None:
        try:
            return shutil.disk_usage(self.settings.output_dir).free
        except OSError:
            return None

    def _free_space_check(self) -> None:
        free = self.free_space()
        if free is not None and free < 500 * 1024 * 1024:
            raise RecordingError(f"Only {format_size(free)} free on the output drive. "
                                 "Free some space or choose another folder.")

    @staticmethod
    def _hide_dir(path: Path) -> None:
        if IS_WINDOWS:
            try:
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
            except Exception:
                pass


def recover_unfinished(folder: Path) -> list[Path]:
    """Hidden part folders left by a crash, newest first, so they can be finished later."""
    try:
        found = [entry for entry in folder.glob(".*.parts") if entry.is_dir() and any(entry.glob("part*.mkv"))]
    except OSError:
        return []
    return sorted(found, key=lambda entry: entry.stat().st_mtime, reverse=True)


def finish_unfinished(folder: Path, container: str = "MP4") -> Path:
    """Join the video parts of an interrupted recording (audio WAVs are added unaligned)."""
    parts = sorted(path for path in folder.glob("part*.mkv") if "_final" not in path.stem)
    if not parts:
        raise RecordingError("No recorded parts were found.")
    finished: list[Path] = []
    for part in parts:
        tracks = [AudioTrack(path) for path in sorted(folder.glob(f"{part.stem}_*.wav"))
                  if path.stat().st_size > 44]
        muxed = folder / f"{part.stem}_final.mkv"
        length = probe_duration(part)
        try:
            result = _run(build_part_mux_command(part, tracks, muxed, length),
                          timeout=max(120.0, (length or 600.0) * 2))
            ok = result.returncode == 0 and muxed.exists() and muxed.stat().st_size > 0
        except Exception:
            ok = False
        finished.append(muxed if ok else part)
    stem = folder.name.lstrip(".").rsplit(".parts", 1)[0] or default_file_stem()
    suffix = ".mkv" if container.upper() == "MKV" else ".mp4"
    output = unique_path(folder.parent / f"{stem} (recovered){suffix}")
    parts_list = folder / "parts.txt"
    parts_list.write_text(concat_list_text(finished), encoding="utf-8")
    result = _run(build_join_command(parts_list, output), timeout=3600)
    if result.returncode != 0 or not output.exists():
        raise RecordingError(result.stderr[-800:] or "Could not join the recorded parts.")
    shutil.rmtree(folder, ignore_errors=True)
    return output


