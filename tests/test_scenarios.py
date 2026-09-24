"""Tests for deterministic scenario detection, persistence, and editing operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.app import create_app_context
from src.config import AppSettings
from src.projects import ProjectCreate
from src.recording import EventSource, EventType, RawEvent
from src.scenarios import ScenarioError


def _seed_two_workflow_session(tmp_settings: AppSettings) -> tuple[str, list[RawEvent]]:
    ctx = create_app_context(tmp_settings)
    try:
        project = ctx.project_service.create_project(
            ProjectCreate(name="Shop App", base_url="https://shop.test")
        )
        session = ctx.session_service.create_session(
            project.id, start_url="https://shop.test/login"
        )
        ctx.session_service.start_session(session.id)
        ctx.session_service.register_page(
            session.id, page_id="p1", tab_index=0, url="https://shop.test/login", title="Login"
        )

        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        raw_events = [
            # Workflow 1: Login
            RawEvent(
                id="r1",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.NAVIGATION,
                source=EventSource.PLAYWRIGHT,
                timestamp=base,
                sequence_no=1,
                payload={"url": "https://shop.test/login", "title": "Login"},
            ),
            RawEvent(
                id="r2",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.INPUT,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=500),
                sequence_no=2,
                target={"tag": "input", "id": "user", "css_path": "#user"},
                payload={"value": "moiz", "is_trusted": True},
            ),
            RawEvent(
                id="r3",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.CLICK,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=900),
                sequence_no=3,
                target={"tag": "button", "text": "Login", "css_path": "#login"},
                payload={"is_trusted": True},
            ),
            RawEvent(
                id="r4",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.SUBMIT,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=950),
                sequence_no=4,
                target={"tag": "form", "id": "login-form", "css_path": "#login-form"},
                payload={"method": "post", "is_trusted": True},
            ),
            RawEvent(
                id="r5",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.NAVIGATION,
                source=EventSource.PLAYWRIGHT,
                timestamp=base + timedelta(milliseconds=1200),
                sequence_no=5,
                payload={"url": "https://shop.test/catalog", "title": "Catalog"},
            ),
            # Workflow 2: Search on catalog
            RawEvent(
                id="r6",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.INPUT,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=4000),
                sequence_no=6,
                target={"tag": "input", "id": "search", "css_path": "#search"},
                payload={"value": "laptop", "is_trusted": True},
            ),
            RawEvent(
                id="r7",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.CLICK,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=4500),
                sequence_no=7,
                target={"tag": "button", "text": "Search", "css_path": "#search-btn"},
                payload={"is_trusted": True},
            ),
            RawEvent(
                id="r8",
                project_id=project.id,
                session_id=session.id,
                page_id="p1",
                event_type=EventType.DOM_MUTATION,
                source=EventSource.PAGE_OBSERVER,
                timestamp=base + timedelta(milliseconds=4700),
                sequence_no=8,
                payload={"notable": [{"tag": "h2", "text": "3 results for laptop"}]},
            ),
        ]
        ctx.event_repository.insert_batch(raw_events)
        ctx.session_service.stop_session(session.id)
        return session.id, raw_events
    finally:
        ctx.close()


def test_scenario_detection_and_editing_preserve_raw_events(tmp_settings: AppSettings) -> None:
    session_id, seeded = _seed_two_workflow_session(tmp_settings)
    ctx = create_app_context(tmp_settings)
    try:
        result, scenarios = ctx.scenario_service.process_session(session_id)
        assert result.raw_event_count == len(seeded)
        assert len(result.processed_events) == 5
        assert len(scenarios) == 2

        assert scenarios[0].name == "Submit: Login"
        assert len(scenarios[0].processed_event_ids) == 3
        assert set(scenarios[0].raw_event_ids) == {"r1", "r2", "r3", "r4", "r5"}

        assert scenarios[1].name == "Flow: Search"
        assert len(scenarios[1].processed_event_ids) == 2
        assert set(scenarios[1].raw_event_ids) == {"r6", "r7", "r8"}

        # Rename & exclude
        renamed = ctx.scenario_service.rename_scenario(
            scenarios[0].id, "User Sign In", description="Recorded login flow"
        )
        assert renamed.name == "User Sign In"
        assert renamed.description == "Recorded login flow"

        excluded = ctx.scenario_service.set_scenario_included(scenarios[1].id, False)
        assert excluded.included is False

        # Reorder
        reordered = ctx.scenario_service.reorder_scenarios(
            session_id, [scenarios[1].id, scenarios[0].id]
        )
        assert [s.id for s in reordered] == [scenarios[1].id, scenarios[0].id]
        assert [s.order_index for s in reordered] == [1, 2]

        # Split a 3-step scenario into (1 step, 2 steps)
        left, right = ctx.scenario_service.split_scenario(
            scenarios[0].id, 1, second_name="Fill & Submit"
        )
        assert len(left.processed_event_ids) == 1
        assert len(right.processed_event_ids) == 2
        assert left.raw_event_ids == ["r1"]
        assert set(right.raw_event_ids) == {"r2", "r3", "r4", "r5"}
        assert right.name == "Fill & Submit"
        assert len(ctx.scenario_service.list_scenarios(session_id)) == 3

        # Merge left & right back together
        merged = ctx.scenario_service.merge_scenarios(
            [left.id, right.id], new_name="Complete Login"
        )
        assert merged.name == "Complete Login"
        assert len(merged.processed_event_ids) == 3
        assert len(ctx.scenario_service.list_scenarios(session_id)) == 2

        # Delete one scenario
        ctx.scenario_service.delete_scenario(scenarios[1].id)
        remaining = ctx.scenario_service.list_scenarios(session_id)
        assert len(remaining) == 1
        assert remaining[0].order_index == 1

        # Invalid split index raises ScenarioError
        with pytest.raises(ScenarioError):
            ctx.scenario_service.split_scenario(remaining[0].id, 0)

        # Immutable raw_events are completely untouched
        persisted_raw = ctx.event_repository.list_by_session(session_id)
        assert [e.id for e in persisted_raw] == [e.id for e in seeded]
    finally:
        ctx.close()
