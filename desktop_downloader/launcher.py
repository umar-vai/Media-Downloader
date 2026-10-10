from __future__ import annotations

import sys

from app import DownloaderApp


class MediaDownloaderApp(DownloaderApp):
    """Media Downloader desktop application."""


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        from self_test import main as self_test_main

        args = [arg for arg in sys.argv[1:] if arg != "--self-test"]
        raise SystemExit(self_test_main(args))
    MediaDownloaderApp().mainloop()
