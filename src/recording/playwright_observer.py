"""Playwright browser-level observer for lifecycle, navigation, network, console, and errors."""

from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from typing import Any

from playwright.sync_api import ConsoleMessage, Page, Request, Response
from playwright.sync_api import Error as PlaywrightError

from src.browser.models import PageInfo
from src.recording.collector import EventCollector
from src.recording.models import EventSource, EventType

logger = logging.getLogger(__name__)


class PlaywrightObserver:
    """Collects browser-level observations from Playwright pages without capturing bodies."""

    def __init__(self, collector: EventCollector) -> None:
        self._collector = collector
        self._request_ids: dict[int, str] = {}

    def record_tab_open(self, info: PageInfo) -> None:
        """Record a newly opened browser tab."""
        self._collector.record(
            event_type=EventType.TAB_OPEN,
            source=EventSource.PLAYWRIGHT,
            page_id=info.page_id,
            payload={
                "tab_index": info.tab_index,
                "url": info.url,
                "title": info.title,
            },
        )

    def record_navigation(self, info: PageInfo) -> None:
        """Record a main-frame URL/title navigation update."""
        self._collector.record(
            event_type=EventType.NAVIGATION,
            source=EventSource.PLAYWRIGHT,
            page_id=info.page_id,
            payload={
                "tab_index": info.tab_index,
                "url": info.url,
                "title": info.title,
            },
        )

    def record_tab_close(self, info: PageInfo) -> None:
        """Record a browser tab closing."""
        self._collector.record(
            event_type=EventType.TAB_CLOSE,
            source=EventSource.PLAYWRIGHT,
            page_id=info.page_id,
            payload={
                "tab_index": info.tab_index,
                "url": info.url,
                "title": info.title,
            },
        )

    def attach_page(self, page_id: str, page: Page) -> None:
        """Attach network, console, and uncaught exception listeners to a Playwright `Page`."""
        page.on("request", lambda req: self._on_request(page_id, req))
        page.on("response", lambda res: self._on_response(page_id, res))
        page.on("requestfailed", lambda req: self._on_request_failed(page_id, req))
        page.on("console", lambda msg: self._on_console(page_id, msg))
        page.on("pageerror", lambda err: self._on_page_error(page_id, err))

    def _on_request(self, page_id: str, request: Request) -> None:
        correlation_id = uuid.uuid4().hex
        self._request_ids[id(request)] = correlation_id
        self._collector.record(
            event_type=EventType.REQUEST,
            source=EventSource.PLAYWRIGHT,
            page_id=page_id,
            correlation_id=correlation_id,
            payload={
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "is_navigation_request": request.is_navigation_request(),
            },
        )

    def _on_response(self, page_id: str, response: Response) -> None:
        request = response.request
        correlation_id = self._request_ids.pop(id(request), None)
        content_type = ""
        with suppress(PlaywrightError):
            content_type = response.headers.get("content-type", "")

        self._collector.record(
            event_type=EventType.RESPONSE,
            source=EventSource.PLAYWRIGHT,
            page_id=page_id,
            correlation_id=correlation_id,
            payload={
                "method": request.method,
                "url": response.url,
                "status": response.status,
                "status_text": response.status_text,
                "ok": response.ok,
                "resource_type": request.resource_type,
                "content_type": content_type,
            },
        )

    def _on_request_failed(self, page_id: str, request: Request) -> None:
        correlation_id = self._request_ids.pop(id(request), None)
        self._collector.record(
            event_type=EventType.REQUEST_FAILED,
            source=EventSource.PLAYWRIGHT,
            page_id=page_id,
            correlation_id=correlation_id,
            payload={
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "failure": request.failure or "",
            },
        )

    def _on_console(self, page_id: str, message: ConsoleMessage) -> None:
        location: dict[str, Any] = {}
        with suppress(PlaywrightError):
            raw_loc = message.location
            if isinstance(raw_loc, dict):
                location = {
                    "location_url": raw_loc.get("url", ""),
                    "line_number": raw_loc.get("lineNumber", 0),
                }

        self._collector.record(
            event_type=EventType.CONSOLE,
            source=EventSource.PLAYWRIGHT,
            page_id=page_id,
            payload={
                "level": message.type,
                "text": message.text,
                **location,
            },
        )

    def _on_page_error(self, page_id: str, error: Exception) -> None:
        self._collector.record(
            event_type=EventType.PAGE_ERROR,
            source=EventSource.PLAYWRIGHT,
            page_id=page_id,
            payload={
                "name": type(error).__name__,
                "message": str(error),
            },
        )
