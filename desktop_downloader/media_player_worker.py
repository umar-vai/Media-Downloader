from __future__ import annotations

import base64
import io
import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ffpyplayer.player import MediaPlayer
from PIL import Image


def emit(payload: dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def command_reader(target: "queue.Queue[dict[str, Any]]") -> None:
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                target.put(payload)
    except Exception:
        pass


def encode_frame(image_obj: Any, max_size: tuple[int, int] = (960, 540)) -> bytes:
    width, height = image_obj.get_size()
    planes = image_obj.to_bytearray()
    if not planes:
        return b""
    image = Image.frombytes("RGB", (int(width), int(height)), bytes(planes[0]))
    image.thumbnail(max_size)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=72, optimize=False)
    return buffer.getvalue()


def main() -> int:
    if len(sys.argv) < 2:
        emit({"type": "error", "message": "Missing media path."})
        return 2

    source = Path(sys.argv[1])
    if not source.exists():
        emit({"type": "error", "message": f"Media file not found: {source}"})
        return 3

    commands: "queue.Queue[dict[str, Any]]" = queue.Queue()
    threading.Thread(target=command_reader, args=(commands,), daemon=True).start()

    try:
        player = MediaPlayer(
            str(source),
            ff_opts={
                "sync": "audio",
                "out_fmt": "rgb24",
            },
        )
        player.set_pause(True)
    except Exception as exc:
        emit({"type": "error", "message": f"Could not initialize playback engine: {exc}"})
        return 4

    emit({"type": "ready"})
    emit({"type": "status", "state": "paused"})

    paused = True
    running = True
    last_frame_pts: float | None = None
    last_position_emit = 0.0

    while running:
        try:
            while True:
                command = commands.get_nowait()
                name = str(command.get("cmd") or "")
                try:
                    if name == "play":
                        player.set_pause(False)
                        paused = False
                        emit({"type": "status", "state": "playing"})
                    elif name == "pause":
                        player.set_pause(True)
                        paused = True
                        emit({"type": "status", "state": "paused"})
                    elif name == "seek":
                        seconds = max(0.0, float(command.get("seconds") or 0.0))
                        player.seek(seconds, relative=False, accurate=True)
                        last_frame_pts = None
                    elif name == "volume":
                        value = max(0.0, min(1.0, float(command.get("value") or 0.0)))
                        player.set_volume(value)
                    elif name == "rate":
                        value = max(0.25, min(4.0, float(command.get("value") or 1.0)))
                        applied = False
                        for method_name in ("set_playback_rate", "set_rate"):
                            method = getattr(player, method_name, None)
                            if callable(method):
                                try:
                                    method(value)
                                    applied = True
                                    break
                                except Exception:
                                    continue
                        emit({"type": "rate", "supported": applied, "value": value})
                    elif name == "close":
                        running = False
                        break
                except Exception as exc:
                    emit({"type": "error", "message": f"Playback command '{name}' failed: {exc}"})
        except queue.Empty:
            pass

        if not running:
            break

        delay = 0.02
        try:
            frame, schedule = player.get_frame(force_refresh=paused)
            if frame is not None:
                image_obj, pts = frame
                pts_value = float(pts)
                if last_frame_pts is None or abs(pts_value - last_frame_pts) > 0.0001 or paused:
                    jpeg = encode_frame(image_obj)
                    if jpeg:
                        emit(
                            {
                                "type": "frame",
                                "pts": pts_value,
                                "delay": float(schedule) if isinstance(schedule, (int, float)) else None,
                                "jpeg": base64.b64encode(jpeg).decode("ascii"),
                            }
                        )
                        last_frame_pts = pts_value

            now = time.monotonic()
            if now - last_position_emit >= 0.12:
                try:
                    pts_now = player.get_pts()
                    if pts_now is not None:
                        emit({"type": "position", "pts": float(pts_now)})
                except Exception:
                    pass
                last_position_emit = now

            if isinstance(schedule, (int, float)):
                delay = max(0.005, min(0.08, float(schedule)))
            elif paused:
                delay = 0.06
        except Exception as exc:
            emit({"type": "error", "message": f"Playback loop failed: {exc}"})
            delay = 0.08

        time.sleep(delay)

    try:
        player.set_pause(True)
    except Exception:
        pass
    try:
        player.close_player()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
