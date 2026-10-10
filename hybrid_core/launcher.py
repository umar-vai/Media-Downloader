from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 38477
URL = f"http://{HOST}:{PORT}"


def open_when_ready() -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{URL}/api/health", timeout=0.4) as response:
                if response.status == 200:
                    webbrowser.open(URL)
                    return
        except Exception:
            time.sleep(0.15)


def main() -> None:
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(
        "hybrid_core.server:app",
        host=HOST,
        port=PORT,
        log_level="warning",
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
