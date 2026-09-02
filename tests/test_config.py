"""Tests for basic application configuration and bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app import bootstrap
from src.config import AppSettings, configure_logging


def test_default_settings_resolve_paths(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path)
    assert settings.resolved_data_dir == tmp_path.resolve()
    assert settings.db_path == tmp_path.resolve() / "autotest.db"
    assert settings.artifacts_dir == tmp_path.resolve() / "artifacts"
    assert settings.logs_dir == tmp_path.resolve() / "logs"


def test_env_overrides_are_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOTEST_ENV", "production")
    monkeypatch.setenv("AUTOTEST_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AUTOTEST_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("AUTOTEST_BROWSER_HEADLESS", "true")
    monkeypatch.setenv("AUTOTEST_EVENT_BATCH_SIZE", "125")

    settings = AppSettings()
    assert settings.env == "production"
    assert settings.log_level == "WARNING"
    assert settings.resolved_data_dir == (tmp_path / "custom").resolve()
    assert settings.browser_headless is True
    assert settings.event_batch_size == 125


def test_bootstrap_creates_directories_and_log_file(tmp_settings: AppSettings) -> None:
    active = bootstrap(tmp_settings)
    assert active.resolved_data_dir.is_dir()
    assert active.artifacts_dir.is_dir()
    assert active.logs_dir.is_dir()
    assert (active.logs_dir / "autotest.log").is_file()


def test_configure_logging_is_idempotent(tmp_settings: AppSettings) -> None:
    logger1 = configure_logging(tmp_settings)
    logger2 = configure_logging(tmp_settings)
    assert logger1 is logger2
    assert len(logger2.handlers) == 2
