from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def connect_pipe(path: str, process: subprocess.Popen[bytes], timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"mpv exited before IPC became available: {process.returncode}")
        try:
            return open(path, "r+b", buffering=0)
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"Could not connect to mpv IPC: {last_error}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: smoke_mpv.py <mpv-exe>")

    mpv = Path(sys.argv[1]).resolve()
    if not mpv.exists():
        raise SystemExit(f"mpv.exe not found: {mpv}")

    version = subprocess.run(
        [str(mpv), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    if version.returncode != 0 or "mpv" not in version.stdout.lower():
        raise SystemExit("mpv --version failed.")

    with tempfile.TemporaryDirectory(prefix="md-mpv-smoke-") as temp:
        media = Path(temp) / "sample.mp4"
        make_sample(media)

        decode = subprocess.run(
            [
                str(mpv),
                "--no-config",
                "--vo=null",
                "--ao=null",
                "--frames=1",
                "--really-quiet",
                str(media),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        if decode.returncode != 0:
            raise SystemExit(
                "mpv decode smoke test failed: "
                + decode.stderr.decode("utf-8", errors="replace")[-800:]
            )

        pipe = rf"\\.\pipe\md_mpv_smoke_{os.getpid()}_{uuid.uuid4().hex}"
        process = subprocess.Popen(
            [
                str(mpv),
                "--no-config",
                "--idle=yes",
                "--keep-open=yes",
                "--pause=yes",
                "--vo=null",
                "--ao=null",
                "--terminal=no",
                "--msg-level=all=no",
                f"--input-ipc-server={pipe}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )

        ipc = None
        reader_done = threading.Event()
        state = {"loaded": False, "position": 0.0}

        def reader() -> None:
            buffer = b""
            try:
                while ipc is not None and not reader_done.is_set():
                    chunk = ipc.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        if not raw.strip():
                            continue
                        try:
                            message = json.loads(raw.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            continue
                        if message.get("event") == "file-loaded":
                            state["loaded"] = True
                        if message.get("event") == "property-change" and message.get("name") == "time-pos":
                            try:
                                state["position"] = float(message.get("data") or 0.0)
                            except (TypeError, ValueError):
                                pass
            except Exception:
                pass

        try:
            ipc = connect_pipe(pipe, process)
            threading.Thread(target=reader, daemon=True).start()

            def send(command: list[object]) -> None:
                payload = (json.dumps({"command": command}, separators=(",", ":")) + "\n").encode("utf-8")
                ipc.write(payload)

            send(["observe_property", 1, "time-pos"])
            send(["loadfile", str(media), "replace"])

            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and not state["loaded"]:
                time.sleep(0.05)
            if not state["loaded"]:
                raise RuntimeError("mpv IPC did not report file-loaded.")

            send(["set_property", "pause", False])
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and state["position"] <= 0.05:
                time.sleep(0.05)
            if state["position"] <= 0.05:
                raise RuntimeError("mpv IPC playback position did not advance.")

            send(["quit"])
        finally:
            reader_done.set()
            try:
                if ipc is not None:
                    ipc.close()
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except Exception:
                process.kill()

    print("mpv playback smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
