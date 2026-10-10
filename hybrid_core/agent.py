from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import queue
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from PIL import Image, ImageDraw
import pystray

from .agent_control import (
    CONTROL_HOST,
    CONTROL_PORT,
    CORE_HOST,
    CORE_PORT,
    CORE_URL,
    MUTEX_NAME,
    can_self_update,
    executable_path,
    send_control,
    updater_helper_path,
)
from .paths import DEFAULT_DOWNLOAD_DIR, SETTINGS_FILE, UPDATE_DIR, WEB_DIR
from .settings_store import SettingsStore
from .version import CORE_VERSION


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ERROR_ALREADY_EXISTS = 183


class WindowsMutex:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError("Could not create Local Core instance mutex.")
        self.handle = handle
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def wait_until_ready(timeout: float = 18.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{CORE_URL}/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def open_app() -> None:
    webbrowser.open(CORE_URL)


def make_tray_icon() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (11, 19, 35, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=15, fill=(118, 87, 255, 255))
    draw.polygon([(20, 20), (44, 20), (44, 31), (51, 31), (32, 48), (13, 31), (20, 31)], fill=(247, 250, 255, 255))
    return image


class Agent:
    def __init__(self, *, background: bool = False) -> None:
        self.background = background
        self.settings_store = SettingsStore(SETTINGS_FILE, default_download_dir=DEFAULT_DOWNLOAD_DIR)
        self.settings = self.settings_store.get()
        self.child: subprocess.Popen[Any] | None = None
        self.icon: pystray.Icon | None = None
        self.control_server: socketserver.ThreadingTCPServer | None = None
        self.control_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.command_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def child_command(self) -> list[str]:
        if bool(getattr(sys, "frozen", False)):
            return [str(executable_path()), "--server-child"]
        return [sys.executable, "-m", "hybrid_core.agent", "--server-child"]

    def start_child(self) -> None:
        if self.child and self.child.poll() is None:
            return
        self.child = subprocess.Popen(
            self.child_command(),
            cwd=str(Path.cwd()),
            close_fds=True,
            creationflags=CREATE_NO_WINDOW,
        )

    def stop_child(self) -> None:
        child = self.child
        self.child = None
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)

    def restart_child(self) -> None:
        self.stop_child()
        self.settings = self.settings_store.get()
        self.start_child()
        threading.Thread(target=self._open_after_ready, daemon=True).start()

    def _open_after_ready(self) -> None:
        if wait_until_ready():
            open_app()

    def open_downloads(self) -> None:
        directory = Path(str(self.settings_store.get()["download_dir"])).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(directory))

    def _validate_staged_update(self, raw: str) -> Path:
        candidate = Path(raw).expanduser().resolve()
        root = UPDATE_DIR.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Staged update is outside the Local Core update directory.") from exc
        if candidate.name.lower() != "mediadownloadercore.exe" or not candidate.is_file():
            raise ValueError("The staged Local Core executable is missing.")
        return candidate

    def apply_update(self, raw_path: str) -> None:
        if not can_self_update():
            raise RuntimeError("Self-update requires the installed Media Downloader Core agent.")
        staged = self._validate_staged_update(raw_path)
        helper = updater_helper_path()
        target = executable_path()
        self.stop_child()
        subprocess.Popen(
            [
                str(helper),
                "--wait-pid",
                str(os.getpid()),
                "--source",
                str(staged),
                "--target",
                str(target),
            ],
            cwd=str(target.parent),
            close_fds=True,
            creationflags=CREATE_NO_WINDOW,
        )
        self.stop_event.set()
        if self.icon:
            self.icon.stop()

    def schedule_quit(self) -> None:
        self.stop_event.set()
        if self.icon:
            self.icon.stop()

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("command") or "")
        if command == "ping":
            return {"ok": True, "version": CORE_VERSION, "pid": os.getpid()}
        if command == "open_app":
            threading.Thread(target=open_app, daemon=True).start()
            return {"ok": True}
        if command == "open_downloads":
            threading.Thread(target=self.open_downloads, daemon=True).start()
            return {"ok": True}
        if command == "restart":
            threading.Thread(target=self.restart_child, daemon=True).start()
            return {"ok": True}
        if command == "quit":
            threading.Timer(0.15, self.schedule_quit).start()
            return {"ok": True}
        if command == "apply_update":
            path = str(payload.get("path") or "")
            if not can_self_update():
                raise RuntimeError("Self-update requires the installed Media Downloader Core agent.")
            staged = self._validate_staged_update(path)
            threading.Timer(0.2, lambda: self.apply_update(str(staged))).start()
            return {"ok": True}
        raise ValueError(f"Unknown agent command: {command}")

    def start_control_server(self) -> None:
        agent = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    line = self.rfile.readline(131072)
                    payload = json.loads(line.decode("utf-8"))
                    response = agent.handle_command(payload if isinstance(payload, dict) else {})
                except Exception as exc:
                    response = {"ok": False, "error": str(exc)}
                self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.control_server = Server((CONTROL_HOST, CONTROL_PORT), Handler)
        self.control_thread = threading.Thread(
            target=self.control_server.serve_forever,
            daemon=True,
            name="media-core-agent-control",
        )
        self.control_thread.start()

    def stop_control_server(self) -> None:
        if self.control_server:
            self.control_server.shutdown()
            self.control_server.server_close()
            self.control_server = None

    def tray_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open Media Downloader", lambda _icon, _item: open_app(), default=True),
            pystray.MenuItem("Open Downloads", lambda _icon, _item: self.open_downloads()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Local Core", lambda _icon, _item: self.restart_child()),
            pystray.MenuItem("Quit", lambda _icon, _item: self.schedule_quit()),
        )

    def _watch_child(self) -> None:
        while not self.stop_event.wait(1.0):
            child = self.child
            if child is not None and child.poll() is not None:
                self.child = None
                self.start_child()

    def run(self) -> int:
        self.start_control_server()
        self.start_child()
        threading.Thread(target=self._watch_child, daemon=True, name="media-core-child-watch").start()
        should_open = bool(self.settings.get("open_browser_on_start", True)) and not self.background
        if should_open:
            threading.Thread(target=self._open_after_ready, daemon=True).start()

        tray_enabled = bool(self.settings.get("tray_icon_enabled", True))
        if tray_enabled:
            self.icon = pystray.Icon(
                "MediaDownloaderCore",
                make_tray_icon(),
                f"Media Downloader Core v{CORE_VERSION}",
                self.tray_menu(),
            )
            try:
                self.icon.run()
            except Exception:
                self.icon = None
                while not self.stop_event.wait(0.5):
                    pass
            finally:
                self.stop_event.set()
        else:
            try:
                while not self.stop_event.wait(0.5):
                    if self.child and self.child.poll() is not None:
                        self.start_child()
            except KeyboardInterrupt:
                self.stop_event.set()

        self.stop_child()
        self.stop_control_server()
        return 0


def run_server_child() -> int:
    uvicorn.run(
        "hybrid_core.server:app",
        host=CORE_HOST,
        port=CORE_PORT,
        log_level="warning",
        reload=False,
        access_log=False,
    )
    return 0


def self_test(report: str | None = None) -> int:
    payload = {
        "ok": WEB_DIR.joinpath("index.html").is_file(),
        "version": CORE_VERSION,
        "web_dir": str(WEB_DIR),
        "packaged": bool(getattr(sys, "frozen", False)),
        "can_self_update": can_self_update(),
        "control_port": CONTROL_PORT,
        "core_port": CORE_PORT,
    }
    if report:
        Path(report).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--server-child", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--updated", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.server_child:
        return run_server_child()
    if args.self_test:
        return self_test(args.report)

    mutex = WindowsMutex(MUTEX_NAME)
    if not mutex.acquire():
        try:
            send_control("open_app")
        except Exception:
            open_app()
        mutex.close()
        return 0

    try:
        return Agent(background=bool(args.background)).run()
    finally:
        mutex.close()


if __name__ == "__main__":
    raise SystemExit(main())
