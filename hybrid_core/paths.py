from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web_pwa"

APP_DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Media Downloader"

STATE_FILE = APP_DATA_DIR / "hybrid-jobs.json"
LOG_FILE = APP_DATA_DIR / "hybrid-core.log"
SETTINGS_FILE = APP_DATA_DIR / "hybrid-settings.json"
EDITOR_LIBRARY_FILE = APP_DATA_DIR / "hybrid-editor-library.json"
UPDATE_DIR = APP_DATA_DIR / "updates" / "local-core"
DIAGNOSTICS_DIR = APP_DATA_DIR / "diagnostics"
