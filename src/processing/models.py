"""Models for processed interactions, correlated outcomes, and processing configuration."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProcessedActionType(StrEnum):
    """Normalized high-level user/browser action types produced by the processing engine."""

    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    SUBMIT = "submit"
    PRESS_KEY = "press_key"
    HOVER = "hover"
    SCROLL = "scroll"
    OPEN_TAB = "open_tab"
    CLOSE_TAB = "close_tab"


class OutcomeType(StrEnum):
    """Categories of observed browser/application outcomes correlated to an action."""

    NAVIGATION = "navigation"
    NETWORK = "network"
    DOM_CHANGE = "dom_change"
    CONSOLE_ERROR = "console_error"
    PAGE_ERROR = "page_error"


class CorrelatedOutcome(BaseModel):
    """An observed outcome linked back to one or more immutable raw events."""

    outcome_type: OutcomeType
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    raw_event_ids: list[str] = Field(default_factory=list)
    timestamp: datetime


class ProcessedEvent(BaseModel):
    """A filtered, grouped, and outcome-correlated interaction step."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str
    page_id: str | None = None
    order_index: int = Field(ge=1)
    action_type: ProcessedActionType
    summary: str
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    outcomes: list[CorrelatedOutcome] = Field(default_factory=list)
    raw_event_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime


class ProcessingConfig(BaseModel):
    """Deterministic tuning parameters for raw event processing."""

    correlation_window_ms: int = Field(default=2500, ge=100, le=30000)
    input_merge_window_ms: int = Field(default=3000, ge=100, le=30000)
    hover_mutation_window_ms: int = Field(default=600, ge=100, le=5000)
    drop_untrusted: bool = True
    keep_scrolls: bool = False
    keep_hovers: bool = False


class ProcessingResult(BaseModel):
    """Output of processing a recording session's raw events."""

    session_id: str
    raw_event_count: int
    processed_events: list[ProcessedEvent] = Field(default_factory=list)
    dropped_count: int = 0
    uncorrelated_outcomes: list[CorrelatedOutcome] = Field(default_factory=list)
