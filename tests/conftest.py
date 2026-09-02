"""Shared pytest fixtures for AutoTest."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AppSettings


@pytest.fixture
def tmp_settings(tmp_path: Path) -> AppSettings:
    """Provide isolated AppSettings backed by a temporary directory."""
    settings = AppSettings(
        env="test",
        log_level="DEBUG",
        data_dir=tmp_path / "autotest_data",
    )
    settings.ensure_directories()
    return settings
