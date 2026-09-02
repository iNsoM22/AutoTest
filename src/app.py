"""Application bootstrap and entry point."""

from __future__ import annotations

import logging

from src import __version__
from src.config import AppSettings, configure_logging, get_settings

logger = logging.getLogger(__name__)


def bootstrap(settings: AppSettings | None = None) -> AppSettings:
    """Initialize application directories, configuration, and logging."""
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    configure_logging(active_settings)
    logger.info(
        "AutoTest v%s initialized (env=%s, data_dir=%s)",
        __version__,
        active_settings.env,
        active_settings.resolved_data_dir,
    )
    return active_settings


def main() -> int:
    """CLI / desktop entry point."""
    bootstrap()
    logger.info("Core configuration ready. Desktop UI shell will be mounted in Phase 1.")
    return 0
