from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "codex_usage_monitor"


def configure_logging(directory: Path | None = None) -> logging.Logger:
    """Configure a small persistent error log without duplicating handlers."""
    log_directory = directory or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Codex-Usage-Monitor" / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "app.log"
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, "baseFilename", None) == str(log_path) for handler in logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        logger.addHandler(handler)
    return logger
