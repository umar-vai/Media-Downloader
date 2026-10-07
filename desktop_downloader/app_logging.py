from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DATA_DIR = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
LOG_DIR = APP_DATA_DIR / "logs"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"MediaDownloader.{name}")
    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=1_500_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / "app.log"
