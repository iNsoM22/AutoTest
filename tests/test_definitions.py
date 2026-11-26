"""Unit and service tests for Phase 4 test definitions, selector ranking, and assertions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.config import AppSettings
from src.definitions import (
    AssertionConfidence,
    AssertionType,
    SelectorStrategy,
    TestAssertion,
    TestDefinitionBuilder,
    TestDefinitionError,
    TestDefinitionRepository,
    TestDefinitionService,
    TestMode,
    build_selector_candidates,
    parse_selector_expression,
)
from src.processing import (
    CorrelatedOutcome,
    OutcomeType,
    ProcessedActionType,
    ProcessedEvent,
    ProcessedEventRepository,
)
from src.projects import (
    ProjectCreate,
    ProjectRepository,
    ProjectService,
    SessionRepository,
    SessionService,
)
from src.recording import EventSource, EventType, RawEvent
from src.scenarios import Scenario, ScenarioRepository, ScenarioService
from src.storage import ArtifactManager, Database, RawEventRepository


def test_selector_ranking_prefers_stable_attributes_and_skips_dynamic_ids() -> None:
    primary, fallbacks = build_selector_candidates(
        {
            "tag": "button",
            "test_id": "login-submit",
            "aria_label": "Sign in",
            "text": "Sign in",
            "id": "react-12345",
            "name": "submit",
            "css_path": "form > button:nth-child(2)",
        }
    )
    assert primary is not None
    assert primary.strategy == SelectorStrategy.TEST_ID
    assert primary.value == "login-submit"
    # Dynamic id 'react-12345' should be excluded; role, label, name, text, css_path remain
    values = [c.value for c in fallbacks]
    assert "#react-12345" not in values
    assert any(c.strategy == SelectorStrategy.ROLE and c.role == "button" for c in fallbacks)

    # Without test_id, role + accessible name wins over placeholder / id / css_path
    primary2, _ = build_selector_candidates(
        {
            "tag": "input",
            "input_type": "email",
            "aria_label": "Work Email",
            "placeholder": "you@company.com",
            "id": "email-input",
            "css_path": "input#email-input",
        }
    )
    assert primary2 is not None
    assert primary2.strategy == SelectorStrategy.ROLE
    assert primary2.role == "textbox"
    assert primary2.name == "Work Email"


def test_parse_selector_expression_supports_all_formats() -> None:
    s1 = parse_selector_expression("test_id=checkout-btn")
    assert s1.strategy == SelectorStrategy.TEST_ID and s1.value == "checkout-btn"

    s2 = parse_selector_expression("role=button[name='Pay Now']")
    assert s2.strategy == SelectorStrategy.ROLE
    assert s2.role == "button" and s2.name == "Pay Now"

    s3 = parse_selector_expression("#custom-id")
    assert s3.strategy == SelectorStrategy.CSS and s3.value == "#custom-id"

    with pytest.raises(ValueError, match="cannot be empty"):
        parse_selector_expression("   ")


def test_builder_classifies_assertions_and_never_treats_http_200_alone_as_success() -> None:
    t0 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    # Scenario 1: Login with redacted password -> correlated network + navigation + DOM heading
    e1 = ProcessedEvent(
        id="pe-1",
        project_id="p1",
        session_id="s1",
        page_id="pg1",
        order_index=1,
        action_type=ProcessedActionType.NAVIGATE,
        summary="Navigate to https://app.example.com/login",
        payload={"url": "https://app.example.com/login"},
        raw_event_ids=["r1"],
        started_at=t0,
        ended_at=t0,
    )
    e2 = ProcessedEvent(
        id="pe-2",
        project_id="p1",
        session_id="s1",
        page_id="pg1",
        order_index=2,
        action_type=ProcessedActionType.FILL,
        summary="Fill 'Password' with '[REDACTED]'",
        target={"tag": "input", "input_type": "password", "name": "password"},
        payload={"value": "[REDACTED]", "redacted": True},
        raw_event_ids=["r2"],
        started_at=t0 + timedelta(seconds=1),
        ended_at=t0 + timedelta(seconds=1),
    )
    e3 = ProcessedEvent(
        id="pe-3",
        project_id="p1",
        session_id="s1",
        page_id="pg1",
        order_index=3,
        action_type=ProcessedActionType.SUBMIT,
        summary="Submit form via 'Login'",
        target={"tag": "form", "id": "login-form"},
        payload={"trigger_target": {"tag": "button", "test_id": "login-btn", "text": "Login"}},
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.NETWORK,
                summary="POST 200 /api/login",
                details={
                    "method": "POST",
                    "url": "https://app.example.com/api/login",
                    "status": 200,
                    "ok": True,
                },
                raw_event_ids=["r3", "r4"],
                timestamp=t0 + timedelta(seconds=2),
            ),
            CorrelatedOutcome(
                outcome_type=OutcomeType.NAVIGATION,
                summary="Navigated to /dashboard",
                details={"url": "https://app.example.com/dashboard", "title": "Dashboard"},
                raw_event_ids=["r5"],
                timestamp=t0 + timedelta(seconds=2, milliseconds=200),
            ),
            CorrelatedOutcome(
                outcome_type=OutcomeType.DOM_CHANGE,
                summary="DOM: Welcome back",
                details={"notable": [{"tag": "h1", "text": "Welcome back"}]},
                raw_event_ids=["r6"],
                timestamp=t0 + timedelta(seconds=2, milliseconds=300),
            ),
        ],
        raw_event_ids=["r3", "r4", "r5", "r6"],
        started_at=t0 + timedelta(seconds=2),
        ended_at=t0 + timedelta(seconds=2, milliseconds=300),
    )

    # Scenario 2: Action with HTTP 200 ONLY (no navigation, no visible DOM change)
    e4 = ProcessedEvent(
        id="pe-4",
        project_id="p1",
        session_id="s1",
        page_id="pg1",
        order_index=4,
        action_type=ProcessedActionType.CLICK,
        summary="Click 'Sync'",
        target={"tag": "button", "test_id": "sync-btn", "text": "Sync"},
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.NETWORK,
                summary="POST 200 /api/sync",
                details={
                    "method": "POST",
                    "url": "https://app.example.com/api/sync",
                    "status": 200,
                    "ok": True,
                },
                raw_event_ids=["r7", "r8"],
                timestamp=t0 + timedelta(seconds=5),
            )
        ],
        raw_event_ids=["r7", "r8"],
        started_at=t0 + timedelta(seconds=5),
        ended_at=t0 + timedelta(seconds=5),
    )

    sc1 = Scenario(
        id="sc-1",
        project_id="p1",
        session_id="s1",
        name="User Login",
        order_index=1,
        included=True,
        processed_event_ids=["pe-1", "pe-2", "pe-3"],
        raw_event_ids=["r1", "r2", "r3", "r4", "r5", "r6"],
    )
    sc2 = Scenario(
        id="sc-2",
        project_id="p1",
        session_id="s1",
        name="Trigger Sync",
        order_index=2,
        included=True,
        processed_event_ids=["pe-4"],
        raw_event_ids=["r7", "r8"],
    )

    defs = TestDefinitionBuilder().build(
        project_id="p1",
        session_id="s1",
        scenarios=[sc1, sc2],
        timeline=[e1, e2, e3, e4],
        base_url="https://app.example.com",
        session_start_url="https://app.example.com/login",
        include_journey=True,
    )

    # 2 independent tests + 1 complete journey test
    assert len(defs) == 3
    login_def, sync_def, journey_def = defs
    assert login_def.mode == TestMode.INDEPENDENT
    assert sync_def.mode == TestMode.INDEPENDENT
    assert journey_def.mode == TestMode.JOURNEY
    assert journey_def.scenario_id is None

    # Redacted input preserved safely
    fill_action = login_def.actions[1]
    assert fill_action.is_redacted is True
    assert fill_action.value == ""

    # Login assertions: correlated URL + directly observed visible heading
    assert len(login_def.assertions) == 2
    assert login_def.assertions[0].assertion_type == AssertionType.URL_CONTAINS
    assert login_def.assertions[0].confidence == AssertionConfidence.CORRELATED
    assert login_def.assertions[0].enabled is True
    assert login_def.assertions[1].assertion_type == AssertionType.TEXT_VISIBLE
    assert login_def.assertions[1].confidence == AssertionConfidence.DIRECTLY_OBSERVED
    assert login_def.assertions[1].expected == "Welcome back"

    # Second independent test declares explicit entry_url + authenticated_session prerequisites
    prereq_names = {p.name for p in sync_def.prerequisites}
    assert {"entry_url", "authenticated_session"} == prereq_names

    # HTTP 200 alone -> inferred, needs_review=True, enabled=False!
    assert len(sync_def.assertions) == 1
    net_only = sync_def.assertions[0]
    assert net_only.assertion_type == AssertionType.NETWORK_RESPONSE
    assert net_only.confidence == AssertionConfidence.INFERRED
    assert net_only.needs_review is True
    assert net_only.enabled is False


def test_definition_service_crud_and_layer_isolation(tmp_settings: AppSettings) -> None:
    db = Database(tmp_settings.db_path)
    try:
        db.initialize()
        artifacts = ArtifactManager(tmp_settings.artifacts_dir)
        project_repo = ProjectRepository(db)
        session_repo = SessionRepository(db)
        raw_repo = RawEventRepository(db)
        processed_repo = ProcessedEventRepository(db)
        scenario_repo = ScenarioRepository(db)
        def_repo = TestDefinitionRepository(db)

        project_svc = ProjectService(project_repo, artifacts)
        session_svc = SessionService(session_repo, project_repo, artifacts)
        scenario_svc = ScenarioService(session_repo, raw_repo, processed_repo, scenario_repo)
        def_svc = TestDefinitionService(
            project_repo, session_repo, processed_repo, scenario_repo, def_repo
        )

        project = project_svc.create_project(
            ProjectCreate(name="Shop", base_url="https://shop.example.com")
        )
        session = session_svc.create_session(project.id, start_url="https://shop.example.com")
        session_svc.register_page(session.id, page_id="p1", tab_index=0, url=session.start_url)

        t0 = datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC)
        raw_repo.insert_batch(
            [
                RawEvent(
                    project_id=project.id,
                    session_id=session.id,
                    page_id="p1",
                    event_type=EventType.NAVIGATION,
                    source=EventSource.PLAYWRIGHT,
                    timestamp=t0,
                    sequence_no=1,
                    payload={"url": "https://shop.example.com/products"},
                ),
                RawEvent(
                    project_id=project.id,
                    session_id=session.id,
                    page_id="p1",
                    event_type=EventType.CLICK,
                    source=EventSource.PAGE_OBSERVER,
                    timestamp=t0 + timedelta(seconds=1),
                    sequence_no=2,
                    target={"tag": "button", "test_id": "add-cart", "text": "Add to cart"},
                    payload={"is_trusted": True},
                ),
                RawEvent(
                    project_id=project.id,
                    session_id=session.id,
                    page_id="p1",
                    event_type=EventType.DOM_MUTATION,
                    source=EventSource.PAGE_OBSERVER,
                    timestamp=t0 + timedelta(seconds=1, milliseconds=200),
                    sequence_no=3,
                    payload={"notable": [{"tag": "div", "text": "Added to cart"}]},
                ),
            ]
        )

        # Cannot generate before processing
        with pytest.raises(TestDefinitionError, match="No scenarios found"):
            def_svc.generate_from_session(session.id)

        scenario_svc.process_session(session.id)
        definitions = def_svc.generate_from_session(session.id, include_journey=True)
        assert len(definitions) == 2

        indep = definitions[0]
        renamed = def_svc.update_metadata(
            indep.id, name="Add Item To Cart", description="Cart flow"
        )
        assert renamed.function_name == "test_add_item_to_cart"
        assert renamed.description == "Cart flow"

        # Edit selector & reorder actions
        edited = def_svc.update_action(
            renamed.id,
            renamed.actions[1].id,
            selector=parse_selector_expression("test_id=buy-now"),
        )
        assert edited.actions[1].selector is not None
        assert edited.actions[1].selector.value == "buy-now"

        moved = def_svc.move_action(edited.id, edited.actions[1].id, -1)
        assert moved.actions[0].action_type == ProcessedActionType.CLICK

        # Add and toggle assertions
        with_added = def_svc.add_assertion(
            moved.id,
            TestAssertion(
                order_index=99,
                assertion_type=AssertionType.TEXT_VISIBLE,
                description="Verify cart count",
                expected="1 item",
            ),
        )
        assert len(with_added.assertions) == 2
        toggled = def_svc.update_assertion(
            with_added.id, with_added.assertions[0].id, enabled=False
        )
        assert toggled.assertions[0].enabled is False

        # Layer 1 (raw_events) and Layer 2 (scenarios) remain completely untouched!
        assert raw_repo.count_by_session(session.id) == 3
        assert len(scenario_svc.list_scenarios(session.id)) == 1
    finally:
        db.close_all()


def test_selectors_and_builder_handle_labels_contenteditable_redacted_urls_and_hidden_dom() -> None:
    # 1. Associated <label> produces ROLE + LABEL candidates
    primary, fallbacks = build_selector_candidates(
        {"tag": "input", "input_type": "text", "label": "Full Name", "id": "full-name"}
    )
    assert primary is not None
    assert primary.strategy == SelectorStrategy.ROLE
    assert primary.role == "textbox"
    assert primary.name == "Full Name"
    assert any(c.strategy == SelectorStrategy.LABEL and c.value == "Full Name" for c in fallbacks)

    # 2. data-test / data-qa map to CSS attribute selectors; contenteditable maps to textbox role
    qa_primary, _ = build_selector_candidates(
        {"tag": "button", "test_id": "save-order", "test_id_attr": "data-qa"}
    )
    assert qa_primary is not None
    assert qa_primary.strategy == SelectorStrategy.CSS
    assert qa_primary.value == '[data-qa="save-order"]'

    ce_primary, _ = build_selector_candidates(
        {"tag": "div", "contenteditable": True, "aria_label": "Comment body"}
    )
    assert ce_primary is not None
    assert ce_primary.strategy == SelectorStrategy.ROLE
    assert ce_primary.role == "textbox"
    assert ce_primary.name == "Comment body"

    # 3. Redacted URL query params are stripped from URL_CONTAINS; hidden DOM mutations skipped
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    ev = ProcessedEvent(
        id="pe-edge",
        project_id="p1",
        session_id="s1",
        page_id="pg1",
        order_index=1,
        action_type=ProcessedActionType.CLICK,
        summary="Click 'Continue'",
        target={"tag": "button", "text": "Continue"},
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.NAVIGATION,
                summary="Navigated to /callback?token=[REDACTED]",
                details={"url": "https://app.example.com/callback?token=%5BREDACTED%5D&ok=1"},
                raw_event_ids=["r1"],
                timestamp=t0,
            ),
            CorrelatedOutcome(
                outcome_type=OutcomeType.DOM_CHANGE,
                summary="DOM: modal hidden changed",
                details={
                    "notable": [
                        {"tag": "div", "id": "spinner", "attribute": "hidden", "visible": False}
                    ]
                },
                raw_event_ids=["r2"],
                timestamp=t0,
            ),
        ],
        raw_event_ids=["r1", "r2"],
        started_at=t0,
        ended_at=t0,
    )
    sc = Scenario(
        id="sc-edge",
        project_id="p1",
        session_id="s1",
        name="Callback Flow",
        order_index=1,
        processed_event_ids=[ev.id],
    )
    defs = TestDefinitionBuilder().build(
        project_id="p1",
        session_id="s1",
        scenarios=[sc],
        timeline=[ev],
        include_journey=False,
    )
    assert len(defs[0].assertions) == 1
    assert defs[0].assertions[0].assertion_type == AssertionType.URL_CONTAINS
    assert defs[0].assertions[0].expected == "/callback"
    assert "REDACTED" not in defs[0].assertions[0].expected
