"""Models and callback contracts for the dedicated Chromium browser manager."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from src.config import AppSettings


class BrowserState(StrEnum):
    """Lifecycle states of the managed Chromium instance."""

    IDLE = "idle"
    LAUNCHING = "launching"
    RUNNING = "running"
    STOPPING = "stopping"
    CLOSED = "closed"
    ERROR = "error"


class BrowserLaunchConfig(BaseModel):
    """Configuration for launching a dedicated Chromium recording session."""

    headless: bool = False
    slow_mo_ms: int = Field(default=0, ge=0)
    default_timeout_ms: int = Field(default=30_000, gt=0)
    viewport_width: int = Field(default=1280, ge=320)
    viewport_height: int = Field(default=800, ge=240)
    start_url: str = "about:blank"

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        *,
        start_url: str = "",
        headless: bool | None = None,
    ) -> BrowserLaunchConfig:
        """Build a launch config from `AppSettings` with optional overrides."""
        resolved_url = start_url.strip() or "about:blank"
        return cls(
            headless=settings.browser_headless if headless is None else headless,
            slow_mo_ms=settings.browser_slow_mo_ms,
            default_timeout_ms=settings.browser_default_timeout_ms,
            start_url=resolved_url,
        )


class PageInfo(BaseModel):
    """Snapshot of a tracked browser page/tab."""

    page_id: str
    tab_index: int
    url: str = ""
    title: str = ""
    is_closed: bool = False


@dataclass(slots=True)
class BrowserCallbacks:
    """Optional lifecycle callbacks invoked by `BrowserManager`."""

    on_page_opened: Callable[[PageInfo], None] | None = None
    on_page_navigated: Callable[[PageInfo], None] | None = None
    on_page_closed: Callable[[PageInfo], None] | None = None
    on_disconnected: Callable[[], None] | None = None
