from __future__ import annotations

import sys


if __name__ == "__main__" and "--self-test" in sys.argv:
    # Do not initialize Tk/CustomTkinter or the GUI event loop just to run
    # the frozen build's headless self-test.
    from self_test import main as self_test_main

    args = [arg for arg in sys.argv[1:] if arg != "--self-test"]
    raise SystemExit(self_test_main(args))


from app import DownloaderApp


class MediaDownloaderApp(DownloaderApp):
    """Media Downloader desktop application."""


if __name__ == "__main__":
    from single_instance import alert_existing_instance, claim_single_instance

    if not claim_single_instance():
        alert_existing_instance()
        raise SystemExit(0)

    MediaDownloaderApp().mainloop()
