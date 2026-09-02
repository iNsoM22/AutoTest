"""Type-safe application configuration with cross-platform default paths."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from platformdirs import PlatformDirs
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "production", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_APP_NAME = "AutoTest"
_APP_AUTHOR = "AutoTest"


class AppSettings(BaseSettings):
    """Central configuration loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="AUTOTEST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = "development"
    log_level: LogLevel = "INFO"

    # Storage paths
    data_dir: Path | None = Field(
        default=None,
        description="Base directory for SQLite database, artifacts, and logs.",
    )
    db_filename: str = "autotest.db"

    # Dedicated Chromium / Playwright defaults
    browser_headless: bool = False
    browser_slow_mo_ms: int = Field(default=0, ge=0)
    browser_default_timeout_ms: int = Field(default=30_000, gt=0)

    # SQLite operational defaults
    sqlite_wal_mode: bool = True
    event_batch_size: int = Field(default=50, ge=1)

    @property
    def resolved_data_dir(self) -> Path:
        """Return the configured data directory or the OS-appropriate default."""
        if self.data_dir is not None:
            return self.data_dir.expanduser().resolve()
        dirs = PlatformDirs(appname=_APP_NAME, appauthor=_APP_AUTHOR)
        return Path(dirs.user_data_dir)

    @property
    def db_path(self) -> Path:
        """Path to the primary SQLite database file."""
        return self.resolved_data_dir / self.db_filename

    @property
    def artifacts_dir(self) -> Path:
        """Directory for screenshots, traces, videos, and execution reports."""
        return self.resolved_data_dir / "artifacts"

    @property
    def logs_dir(self) -> Path:
        """Directory for rotating application log files."""
        return self.resolved_data_dir / "logs"

    def ensure_directories(self) -> None:
        """Create required local storage directories if they do not exist."""
        self.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached instance of `AppSettings`."""
    return AppSettings()
