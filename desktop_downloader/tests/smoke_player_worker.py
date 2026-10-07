from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


def make_sample(path: Path) -> None:
    command = [
        get_ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x180:rate=15",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        "1.5",
        "-c:v",
        "mpeg4",
        "-q:v",
        "5",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, timeout=30)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: smoke_player_worker.py <worker-exe>")

    worker = Path(sys.argv[1]).resolve()
    if not worker.exists():
        raise SystemExit(f"Worker not found: {worker}")

    with tempfile.TemporaryDirectory(prefix="md-player-smoke-") as temp:
        media = Path(temp) / "sample.mp4"
        make_sample(media)

        process = subprocess.Popen(
            [str(worker), str(media)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        ready = False
        frame_seen = False
        deadline = time.time() + 15
        lines: "queue.Queue[str]" = queue.Queue()

        def read_stdout() -> None:
            if not process.stdout:
                return
            for line in process.stdout:
                lines.put(line)

        threading.Thread(target=read_stdout, daemon=True).start()

        try:
            while time.time() < deadline:
                if process.poll() is not None and lines.empty():
                    break
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    continue
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "ready":
                    ready = True
                    if process.stdin:
                        process.stdin.write('{"cmd":"play"}\n')
                        process.stdin.flush()
                elif message.get("type") == "frame":
                    frame_seen = bool(message.get("jpeg"))
                    if ready and frame_seen:
                        break
        finally:
            try:
                if process.stdin:
                    process.stdin.write('{"cmd":"close"}\n')
                    process.stdin.flush()
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except Exception:
                process.kill()

        if not ready or not frame_seen:
            stderr = ""
            try:
                stderr = process.stderr.read() if process.stderr else ""
            except Exception:
                pass
            raise SystemExit(
                f"Playback worker smoke test failed. ready={ready} frame={frame_seen} "
                f"exit={process.returncode} stderr={stderr[-1200:]}"
            )

    print("Playback worker smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
