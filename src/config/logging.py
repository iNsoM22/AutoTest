"""Structured console and rotating file logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from src.config.settings import AppSettings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3


def configure_logging(settings: AppSettings) -> logging.Logger:
    """Configure root logger for the `src` namespace with console and file handlers."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("src")
    root_logger.setLevel(getattr(logging, settings.log_level))
    root_logger.propagate = False

    # Close and clear existing handlers so repeated calls (e.g., in tests) stay clean.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        filename=settings.logs_dir / "autotest.log",
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return root_logger
