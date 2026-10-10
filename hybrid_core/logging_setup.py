from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(path: Path) -> logging.Logger:
    logger = logging.getLogger("media_downloader.hybrid")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    resolved = str(Path(path).resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and getattr(handler, "baseFilename", "") == resolved:
            return logger

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    return logger
