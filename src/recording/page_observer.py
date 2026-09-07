"""Injected JavaScript page-level observer wired via Playwright context bindings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page

from src.recording.collector import EventCollector
from src.recording.models import EventSource, EventType

logger = logging.getLogger(__name__)

_BINDING_NAME = "__autotestRecordEvent"
_SCRIPT_PATH = Path(__file__).with_name("page_observer.js")
_PAGE_OBSERVER_SCRIPT = _SCRIPT_PATH.read_text(encoding="utf-8")

_ALLOWED_EVENT_TYPES = frozenset(
    {
        EventType.CLICK,
        EventType.INPUT,
        EventType.CHANGE,
        EventType.SUBMIT,
        EventType.KEYDOWN,
        EventType.HOVER,
        EventType.SCROLL,
        EventType.DOM_MUTATION,
    }
)


class PageObserver:
    """Installs the JS interaction & DOM observer on a Playwright `BrowserContext`."""

    def __init__(
        self,
        collector: EventCollector,
        page_id_resolver: Callable[[Page], str | None],
    ) -> None:
        self._collector = collector
        self._page_id_resolver = page_id_resolver

    def attach_context(self, context: BrowserContext) -> None:
        """Expose the Python bridge and inject the observer script into all pages/frames."""
        context.expose_binding(_BINDING_NAME, self._handle_page_event)
        context.add_init_script(_PAGE_OBSERVER_SCRIPT)
        logger.debug("Attached injected JS page observer to browser context")

    def _handle_page_event(self, source: dict[str, Any], raw: object) -> None:
        if not isinstance(raw, dict):
            return

        event_type_str = raw.get("event_type")
        if not isinstance(event_type_str, str):
            return

        try:
            event_type = EventType(event_type_str)
        except ValueError:
            logger.debug("Ignored unknown page observer event type: %r", event_type_str)
            return

        if event_type not in _ALLOWED_EVENT_TYPES:
            return

        page = source.get("page")
        page_id = self._page_id_resolver(page) if page is not None else None

        target = raw.get("target")
        payload = raw.get("payload")
        target_dict = dict(target) if isinstance(target, dict) else {}
        payload_dict = dict(payload) if isinstance(payload, dict) else {}

        frame_url = raw.get("frame_url")
        if isinstance(frame_url, str) and frame_url:
            payload_dict.setdefault("url", frame_url)
        if "is_main_frame" in raw:
            payload_dict["is_main_frame"] = bool(raw["is_main_frame"])

        self._collector.record(
            event_type=event_type,
            source=EventSource.PAGE_OBSERVER,
            page_id=page_id,
            target=target_dict,
            payload=payload_dict,
        )
