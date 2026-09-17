"""Unit tests for deterministic event filtering, grouping, deduplication, and correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.processing import (
    OutcomeType,
    ProcessedActionType,
    ProcessingEngine,
)
from src.recording import EventSource, EventType, RawEvent


def _make_event(
    seq: int,
    event_type: EventType,
    *,
    offset_ms: int = 0,
    source: EventSource = EventSource.PAGE_OBSERVER,
    page_id: str = "page-1",
    target: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> RawEvent:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return RawEvent(
        id=f"ev-{seq}",
        project_id="proj-1",
        session_id="sess-1",
        page_id=page_id,
        event_type=event_type,
        source=source,
        timestamp=base + timedelta(milliseconds=offset_ms),
        sequence_no=seq,
        target=target or {},
        payload=payload or {},
        correlation_id=correlation_id,
    )


def test_processing_filters_noise_and_keeps_meaningful_hover() -> None:
    events = [
        _make_event(
            1,
            EventType.NAVIGATION,
            offset_ms=0,
            source=EventSource.PLAYWRIGHT,
            payload={"url": "https://app.test/home", "title": "Home"},
        ),
        # Static asset request/response -> dropped
        _make_event(
            2,
            EventType.REQUEST,
            offset_ms=50,
            source=EventSource.PLAYWRIGHT,
            correlation_id="c-img",
            payload={
                "method": "GET",
                "url": "https://app.test/logo.png",
                "resource_type": "image",
                "is_navigation_request": False,
            },
        ),
        _make_event(
            3,
            EventType.RESPONSE,
            offset_ms=80,
            source=EventSource.PLAYWRIGHT,
            correlation_id="c-img",
            payload={
                "method": "GET",
                "url": "https://app.test/logo.png",
                "status": 200,
                "ok": True,
                "resource_type": "image",
            },
        ),
        # Console info -> dropped; console error -> kept as outcome
        _make_event(
            4,
            EventType.CONSOLE,
            offset_ms=100,
            source=EventSource.PLAYWRIGHT,
            payload={"level": "log", "text": "debug info"},
        ),
        # Scroll -> dropped by default
        _make_event(
            5,
            EventType.SCROLL,
            offset_ms=150,
            payload={"scroll_x": 0, "scroll_y": 240},
        ),
        # Untrusted synthetic click -> dropped
        _make_event(
            6,
            EventType.CLICK,
            offset_ms=200,
            target={"tag": "button", "text": "Hidden"},
            payload={"is_trusted": False},
        ),
        # Empty DOM mutation -> dropped
        _make_event(
            7,
            EventType.DOM_MUTATION,
            offset_ms=250,
            payload={"added_count": 1, "removed_count": 0, "notable": []},
        ),
        # Hover followed closely by notable DOM mutation -> kept!
        _make_event(
            8,
            EventType.HOVER,
            offset_ms=500,
            target={"tag": "button", "text": "Menu"},
            payload={"is_trusted": True},
        ),
        _make_event(
            9,
            EventType.DOM_MUTATION,
            offset_ms=650,
            payload={
                "added_count": 1,
                "notable": [{"tag": "div", "role": "dialog", "text": "Dropdown Open"}],
            },
        ),
    ]

    result = ProcessingEngine().process("sess-1", events)
    assert result.raw_event_count == 9
    assert result.dropped_count == 6
    assert [e.action_type for e in result.processed_events] == [
        ProcessedActionType.NAVIGATE,
        ProcessedActionType.HOVER,
    ]
    hover_event = result.processed_events[1]
    assert len(hover_event.outcomes) == 1
    assert hover_event.outcomes[0].outcome_type == OutcomeType.DOM_CHANGE
    assert set(hover_event.raw_event_ids) == {"ev-8", "ev-9"}


def test_processing_groups_inputs_collapses_submit_and_correlates_outcomes() -> None:
    email_target = {"tag": "input", "id": "email", "name": "email", "css_path": "#email"}
    checkbox_target = {
        "tag": "input",
        "id": "remember",
        "input_type": "checkbox",
        "css_path": "#remember",
    }
    login_btn = {"tag": "button", "id": "login-btn", "text": "Sign in", "css_path": "#login-btn"}
    form_target = {"tag": "form", "id": "login-form", "css_path": "#login-form"}

    events = [
        _make_event(
            1,
            EventType.TAB_OPEN,
            offset_ms=0,
            source=EventSource.PLAYWRIGHT,
            payload={"tab_index": 0, "url": "https://app.test/login", "title": "Login"},
        ),
        _make_event(
            2,
            EventType.NAVIGATION,
            offset_ms=100,
            source=EventSource.PLAYWRIGHT,
            payload={"tab_index": 0, "url": "https://app.test/login", "title": "Login"},
        ),
        # Rapid inputs + trailing change on same field -> merged into single FILL
        _make_event(
            3,
            EventType.INPUT,
            offset_ms=500,
            target=email_target,
            payload={"value": "alice", "is_trusted": True},
        ),
        _make_event(
            4,
            EventType.INPUT,
            offset_ms=900,
            target=email_target,
            payload={"value": "alice@example.com", "is_trusted": True},
        ),
        _make_event(
            5,
            EventType.CHANGE,
            offset_ms=1050,
            target=email_target,
            payload={"value": "alice@example.com", "is_trusted": True},
        ),
        # Click + change on checkbox -> collapsed into single CHECK
        _make_event(
            6,
            EventType.CLICK,
            offset_ms=1400,
            target=checkbox_target,
            payload={"is_trusted": True},
        ),
        _make_event(
            7,
            EventType.CHANGE,
            offset_ms=1450,
            target=checkbox_target,
            payload={"checked": True, "value": "on", "is_trusted": True},
        ),
        # Click button + form submit -> collapsed into single SUBMIT
        _make_event(
            8,
            EventType.CLICK,
            offset_ms=1800,
            target=login_btn,
            payload={"is_trusted": True},
        ),
        _make_event(
            9,
            EventType.SUBMIT,
            offset_ms=1850,
            target=form_target,
            payload={"action": "/api/login", "method": "post", "is_trusted": True},
        ),
        # Correlated API request + response
        _make_event(
            10,
            EventType.REQUEST,
            offset_ms=1900,
            source=EventSource.PLAYWRIGHT,
            correlation_id="req-login",
            payload={
                "method": "POST",
                "url": "https://app.test/api/login",
                "resource_type": "fetch",
                "is_navigation_request": False,
            },
        ),
        _make_event(
            11,
            EventType.RESPONSE,
            offset_ms=2100,
            source=EventSource.PLAYWRIGHT,
            correlation_id="req-login",
            payload={
                "method": "POST",
                "url": "https://app.test/api/login",
                "status": 200,
                "ok": True,
                "resource_type": "fetch",
            },
        ),
        # Correlated navigation to /dashboard + notable heading
        _make_event(
            12,
            EventType.NAVIGATION,
            offset_ms=2250,
            source=EventSource.PLAYWRIGHT,
            payload={"tab_index": 0, "url": "https://app.test/dashboard", "title": "Dashboard"},
        ),
        _make_event(
            13,
            EventType.DOM_MUTATION,
            offset_ms=2400,
            payload={
                "added_count": 1,
                "notable": [{"tag": "h1", "role": "", "text": "Welcome Alice"}],
            },
        ),
    ]

    result = ProcessingEngine().process("sess-1", events)
    actions = result.processed_events

    assert [a.action_type for a in actions] == [
        ProcessedActionType.NAVIGATE,
        ProcessedActionType.FILL,
        ProcessedActionType.CHECK,
        ProcessedActionType.SUBMIT,
    ]
    assert actions[0].raw_event_ids == ["ev-1", "ev-2"]
    assert actions[1].payload["value"] == "alice@example.com"
    assert actions[1].raw_event_ids == ["ev-3", "ev-4", "ev-5"]
    assert actions[2].raw_event_ids == ["ev-6", "ev-7"]

    submit = actions[3]
    assert submit.payload["trigger_label"] == "Sign in"
    assert [o.outcome_type for o in submit.outcomes] == [
        OutcomeType.NETWORK,
        OutcomeType.NAVIGATION,
        OutcomeType.DOM_CHANGE,
    ]
    assert set(submit.raw_event_ids) == {"ev-8", "ev-9", "ev-10", "ev-11", "ev-12", "ev-13"}
