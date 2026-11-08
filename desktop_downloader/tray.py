"""System-tray presence: the download engine keeps running while the window is hidden."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

try:
    import pystray
    from PIL import Image
except Exception:  # pragma: no cover - tray is optional
    pystray = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]


class TrayController:
    def __init__(
        self,
        *,
        icon_path: Path | None,
        title: str,
        on_show: Callable[[], None],
        on_pause_all: Callable[[], None],
        on_resume_all: Callable[[], None],
        on_quit: Callable[[], None],
        run_on_ui: Callable[[Callable[[], None]], None],
    ) -> None:
        self._icon: Any = None
        self._title = title
        self._status = "Idle"
        self._ui = run_on_ui
        self._on_show = on_show
        self._on_pause_all = on_pause_all
        self._on_resume_all = on_resume_all
        self._on_quit = on_quit
        self._icon_path = icon_path

    @property
    def available(self) -> bool:
        return self._icon is not None

    def start(self) -> bool:
        if pystray is None or Image is None:
            return False
        try:
            image = self._load_image()
            menu = pystray.Menu(
                pystray.MenuItem("Open Media Downloader", self._wrap(self._on_show), default=True),
                pystray.MenuItem(lambda _item: self._status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Pause all", self._wrap(self._on_pause_all)),
                pystray.MenuItem("Resume all", self._wrap(self._on_resume_all)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._wrap(self._on_quit)),
            )
            self._icon = pystray.Icon("MediaDownloader", image, self._title, menu)
            self._icon.run_detached()
            return True
        except Exception:
            self._icon = None
            return False

    def _load_image(self) -> Any:
        if self._icon_path and self._icon_path.exists():
            with Image.open(self._icon_path) as ico:
                best = max(ico.info.get("sizes") or {(256, 256)})
                ico.size = best
                return ico.convert("RGBA").copy()
        return Image.new("RGBA", (64, 64), (124, 108, 255, 255))

    def _wrap(self, action: Callable[[], None]) -> Callable[..., None]:
        def run(_icon: Any = None, _item: Any = None) -> None:
            self._ui(action)

        return run

    def set_status(self, text: str) -> None:
        self._status = text
        if self._icon is not None:
            try:
                self._icon.title = f"{self._title} — {text}"[:120]
                self._icon.update_menu()
            except Exception:
                pass

    def notify(self, message: str, title: str | None = None) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message[:220], title or self._title)
            except Exception:
                pass

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
