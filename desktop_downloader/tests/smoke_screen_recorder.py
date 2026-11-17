"""Records the real screen for a few seconds (with a pause) and checks the result.

Windows uses gdigrab; Linux uses x11grab (run it under Xvfb). Sound capture is checked
only for importability here, since CI machines have no audio devices.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

import screen_recorder_engine as engine
from media_editor_engine import probe_media


def main() -> int:
    print("Encoders:", engine.available_encoders())
    print("Desktop Duplication:", engine.ddagrab_available())
    print("Sound capture:", engine.audio_backend_error() or "available")
    with tempfile.TemporaryDirectory() as directory:
        settings = engine.RecordSettings(region=(0, 0, 320, 240), output_dir=Path(directory), fps=15,
                                         system_audio=False, file_stem="smoke", capture_engine="gdigrab")
        session = engine.RecordingSession(settings, log=print)
        session.start()
        time.sleep(1.5)
        session.pause()
        time.sleep(0.5)
        session.resume()
        time.sleep(1.5)
        output = session.stop()
        info = probe_media(output)
        print("Recorded:", output.name, info, session.warnings)
        if not info.has_video or (info.width, info.height) != (320, 240):
            raise AssertionError(f"Unexpected recording: {info}")
        if not 2.0 <= info.duration <= 8.0:
            raise AssertionError(f"Recording length {info.duration:.2f}s is off")
        if engine.recover_unfinished(Path(directory)):
            raise AssertionError("The part files were not cleaned up.")
    print("Screen recorder smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
