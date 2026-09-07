"""Unified raw event models for Playwright, page observer, and optional CDP sources."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventSource(StrEnum):
    """Origin of a recorded observation."""

    PLAYWRIGHT = "playwright"
    PAGE_OBSERVER = "page_observer"
    CDP = "cdp"


class EventType(StrEnum):
    """Normalized raw event types captured during a recording session."""

    # Page-level interaction & DOM observations
    CLICK = "click"
    INPUT = "input"
    CHANGE = "change"
    SUBMIT = "submit"
    KEYDOWN = "keydown"
    HOVER = "hover"
    SCROLL = "scroll"
    DOM_MUTATION = "dom_mutation"

    # Browser-level lifecycle, navigation, network, and diagnostics
    TAB_OPEN = "tab_open"
    TAB_CLOSE = "tab_close"
    NAVIGATION = "navigation"
    REQUEST = "request"
    RESPONSE = "response"
    REQUEST_FAILED = "request_failed"
    CONSOLE = "console"
    PAGE_ERROR = "page_error"


class RawEvent(BaseModel):
    """Immutable normalized observation persisted to `raw_events`."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str
    page_id: str | None = None
    event_type: EventType
    source: EventSource
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence_no: int = Field(ge=1)
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
