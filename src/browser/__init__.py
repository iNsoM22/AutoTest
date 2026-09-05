"""Dedicated Chromium lifecycle, Playwright controller, and page/tab tracking."""

from src.browser.manager import BrowserManager, BrowserManagerError
from src.browser.models import BrowserCallbacks, BrowserLaunchConfig, BrowserState, PageInfo

__all__ = [
    "BrowserCallbacks",
    "BrowserLaunchConfig",
    "BrowserManager",
    "BrowserManagerError",
    "BrowserState",
    "PageInfo",
]
