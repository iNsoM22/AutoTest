"""Hybrid recording engine coordinating Playwright and injected JS observers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from playwright.sync_api import BrowserContext, Page

from src.browser.models import PageInfo
from src.recording.collector import EventCollector
from src.recording.models import RawEvent
from src.recording.page_observer import PageObserver
from src.recording.playwright_observer import PlaywrightObserver

if TYPE_CHECKING:
    from src.storage.events import RawEventRepository


class RecordingEngine:
    """Attaches hybrid observers to a Playwright browser session and persists raw events."""

    def __init__(
        self,
        event_repository: RawEventRepository,
        *,
        project_id: str,
        session_id: str,
        page_id_resolver: Callable[[Page], str | None],
        batch_size: int = 50,
        on_event: Callable[[RawEvent], None] | None = None,
    ) -> None:
        self._collector = EventCollector(
            event_repository,
            project_id=project_id,
            session_id=session_id,
            batch_size=batch_size,
            on_event=on_event,
        )
        self._playwright_observer = PlaywrightObserver(self._collector)
        self._page_observer = PageObserver(self._collector, page_id_resolver)

    @property
    def collector(self) -> EventCollector:
        """Return the underlying `EventCollector`."""
        return self._collector

    @property
    def event_count(self) -> int:
        """Return total raw events captured in this session."""
        return self._collector.total_recorded

    def attach_context(self, context: BrowserContext) -> None:
        """Install injected JS observers on the newly created `BrowserContext`."""
        self._page_observer.attach_context(context)

    def attach_page(self, page_id: str, page: Page) -> None:
        """Attach Playwright network, console, and error listeners to a page."""
        self._playwright_observer.attach_page(page_id, page)

    def on_page_opened(self, info: PageInfo) -> None:
        """Record a `tab_open` browser event."""
        self._playwright_observer.record_tab_open(info)

    def on_page_navigated(self, info: PageInfo) -> None:
        """Record a `navigation` browser event."""
        self._playwright_observer.record_navigation(info)

    def on_page_closed(self, info: PageInfo) -> None:
        """Record a `tab_close` browser event."""
        self._playwright_observer.record_tab_close(info)

    def flush(self) -> int:
        """Flush any buffered raw events to SQLite."""
        return self._collector.flush()
