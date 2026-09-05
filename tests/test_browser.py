"""Tests for dedicated Chromium browser lifecycle and page/tab tracking."""

from __future__ import annotations

import pytest

from src.browser import (
    BrowserCallbacks,
    BrowserLaunchConfig,
    BrowserManager,
    BrowserManagerError,
    BrowserState,
    PageInfo,
)
from src.config import AppSettings


def test_launch_config_from_settings(tmp_settings: AppSettings) -> None:
    cfg = BrowserLaunchConfig.from_settings(
        tmp_settings,
        start_url="  https://example.com  ",
        headless=True,
    )
    assert cfg.headless is True
    assert cfg.start_url == "https://example.com"
    assert cfg.default_timeout_ms == tmp_settings.browser_default_timeout_ms


def test_open_page_without_launch_raises() -> None:
    manager = BrowserManager()
    assert manager.state == BrowserState.IDLE
    with pytest.raises(BrowserManagerError, match="not running"):
        manager.open_page("about:blank")


@pytest.mark.browser
def test_launch_track_tabs_and_close_chromium() -> None:
    opened: list[PageInfo] = []
    navigated: list[PageInfo] = []
    closed: list[PageInfo] = []

    manager = BrowserManager(
        callbacks=BrowserCallbacks(
            on_page_opened=opened.append,
            on_page_navigated=navigated.append,
            on_page_closed=closed.append,
        )
    )
    page1_url = "data:text/html,<title>Home Page</title><h1>Home</h1>"
    page2_url = "data:text/html,<title>Second Tab</title><h1>Tab 2</h1>"

    try:
        first = manager.launch(BrowserLaunchConfig(headless=True, start_url=page1_url))
        assert manager.is_running
        assert first.tab_index == 0
        assert first.title == "Home Page"

        second = manager.open_page(page2_url)
        assert second.tab_index == 1
        assert second.title == "Second Tab"

        assert len(manager.list_pages()) == 2
        assert len(opened) == 2
        assert any(p.title == "Second Tab" for p in navigated)
    finally:
        manager.close()

    assert manager.state == BrowserState.CLOSED
    assert not manager.is_running
    assert len(closed) >= 2
