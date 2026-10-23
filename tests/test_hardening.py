"""Phase 6 hardening tests: performance, redaction, selector reliability, recovery, diagnostics."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from src.app import create_app_context
from src.config import AppSettings, run_environment_diagnostics
from src.definitions import (
    SelectorStrategy,
    TestDefinitionBuilder,
    build_selector_candidates,
)
from src.execution import ExecutionMode, RunStatus, TestCaseResult, TestResultStatus, TestRun
from src.generator import PytestPlaywrightGenerator
from src.processing import ProcessingEngine
from src.projects import ProjectCreate, SessionStatus
from src.recording import (
    REDACTED_VALUE,
    EventSource,
    EventType,
    RawEvent,
    redact_string,
    redact_target_and_payload,
)
from src.scenarios import ScenarioDetector


def test_hardened_redaction_covers_jwts_api_keys_luhn_cards_and_labels() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureToken12345"
    assert redact_string(f"token={jwt}") == f"token={REDACTED_VALUE}"

    assert redact_string("key=sk-abc1234567890abcdef") == f"key={REDACTED_VALUE}"
    assert redact_string("gh=ghp_1234567890abcdefghij1234") == f"gh={REDACTED_VALUE}"
    assert redact_string("aws=AKIAIOSFODNN7EXAMPLE") == f"aws={REDACTED_VALUE}"

    # Luhn-valid test card number (4111-1111-1111-1111) is redacted; non-Luhn number is preserved
    assert redact_string("Card 4111-1111-1111-1111 charged") == f"Card {REDACTED_VALUE} charged"
    assert redact_string("Order 4111-1111-1111-1112 confirmed") == (
        "Order 4111-1111-1111-1112 confirmed"
    )

    # Sensitive aria_label / placeholder triggers input value redaction even without name/id
    _, payload = redact_target_and_payload(
        {"tag": "input", "input_type": "text", "placeholder": "Enter API Secret Key"},
        {"value": "super-secret-value"},
    )
    assert payload["value"] == REDACTED_VALUE
    assert payload["redacted"] is True


def test_selector_reliability_skips_modern_dynamic_ids_and_escapes_special_ids() -> None:
    dynamic_ids = [
        "550e8400-e29b-41d4-a716-446655440000",
        ":r1a:",
        "mui-42",
        "radix-:r3:",
        "headlessui-listbox-button-9",
        "mat-input-0",
        "cdk-overlay-3",
        "item_deadbeef01",
    ]
    for dyn_id in dynamic_ids:
        primary, fallbacks = build_selector_candidates(
            {"tag": "input", "id": dyn_id, "name": "email"}
        )
        all_values = [c.value for c in ([primary, *fallbacks] if primary else [])]
        assert f"#{dyn_id}" not in all_values
        assert f'[id="{dyn_id}"]' not in all_values

    # Special characters in stable ID use attribute selector instead of invalid `#a.b`
    primary, _ = build_selector_candidates({"tag": "input", "id": "user.email"})
    assert primary is not None
    assert primary.strategy == SelectorStrategy.CSS
    assert primary.value == '[id="user.email"]'

    # Deep nth-child css_path receives lower confidence than shallow semantic path
    shallow, _ = build_selector_candidates({"css_path": "main > button.save"})
    deep, _ = build_selector_candidates(
        {"css_path": "body > div:nth-child(2) > div:nth-child(4) > ul > li:nth-child(3) > a"}
    )
    assert shallow is not None and deep is not None
    assert shallow.confidence > deep.confidence

    # alt / title attributes act as fallbacks when higher-priority attributes are absent
    img_primary, _ = build_selector_candidates({"tag": "img", "alt": "Company Logo"})
    assert img_primary is not None
    assert img_primary.value == 'img[alt="Company Logo"]'


def test_interrupted_session_pages_and_test_runs_recover_on_startup(
    tmp_settings: AppSettings,
) -> None:
    ctx1 = create_app_context(tmp_settings)
    try:
        project = ctx1.project_service.create_project(ProjectCreate(name="CrashApp"))
        session = ctx1.session_service.create_session(project.id, name="InFlight")
        ctx1.session_service.start_session(session.id)
        ctx1.session_service.register_page(
            session.id,
            page_id="open-tab-1",
            tab_index=0,
            url="https://example.com",
            title="Home",
        )

        # Simulate an in-flight test run interrupted by crash
        run = TestRun(
            project_id=project.id,
            session_id=session.id,
            mode=ExecutionMode.INDEPENDENT,
            status=RunStatus.RUNNING,
            total_count=1,
        )
        ctx1.execution_service._execution_repo.create_run(run)
        res = TestCaseResult(
            run_id=run.id,
            function_name="test_in_flight",
            test_name="In Flight",
            status=TestResultStatus.RUNNING,
            order_index=1,
        )
        ctx1.execution_service._execution_repo.create_results([res])
        session_id = session.id
        run_id = run.id
    finally:
        ctx1.close()

    # Restarting app context triggers automatic crash recovery
    ctx2 = create_app_context(tmp_settings)
    try:
        recovered_session = ctx2.session_service.get_session(session_id)
        assert recovered_session.status == SessionStatus.INTERRUPTED
        assert recovered_session.ended_at is not None

        pages = ctx2.session_service.list_pages(session_id)
        assert len(pages) == 1
        assert pages[0].closed_at is not None

        recovered_run, recovered_results = ctx2.execution_service.get_run_with_results(run_id)
        assert recovered_run.status == RunStatus.CANCELLED
        assert recovered_run.ended_at is not None
        assert recovered_results[0].status == TestResultStatus.SKIPPED
        assert "Interrupted" in recovered_results[0].error_message
    finally:
        ctx2.close()


def test_high_volume_5000_events_pipeline_performance(tmp_settings: AppSettings) -> None:
    ctx = create_app_context(tmp_settings)
    try:
        project = ctx.project_service.create_project(
            ProjectCreate(name="PerfApp", base_url="https://perf.example.com")
        )
        session = ctx.session_service.create_session(
            project.id, name="HighVolume", start_url="https://perf.example.com"
        )

        base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        events: list[RawEvent] = []
        seq = 1

        # Build 5,000 realistic mixed events: navigations, clicks, rapid typing, network, DOM noise
        for block in range(50):
            ts = base_ts + timedelta(seconds=block * 2)
            events.append(
                RawEvent(
                    project_id=project.id,
                    session_id=session.id,
                    event_type=EventType.NAVIGATION,
                    source=EventSource.PLAYWRIGHT,
                    timestamp=ts,
                    sequence_no=seq,
                    payload={"url": f"https://perf.example.com/section-{block % 5}"},
                )
            )
            seq += 1

            # 10 rapid input events on the same field (should debounce to 1 fill!)
            for ch_idx in range(10):
                events.append(
                    RawEvent(
                        project_id=project.id,
                        session_id=session.id,
                        event_type=EventType.INPUT,
                        source=EventSource.PAGE_OBSERVER,
                        timestamp=ts + timedelta(milliseconds=50 * (ch_idx + 1)),
                        sequence_no=seq,
                        target={"tag": "input", "test_id": f"search-{block % 5}"},
                        payload={"value": f"query-{block}"[: ch_idx + 1]},
                    )
                )
                seq += 1

            # 1 click + 88 noisy DOM/network events = 100 events per block -> 5,000 total
            events.append(
                RawEvent(
                    project_id=project.id,
                    session_id=session.id,
                    event_type=EventType.CLICK,
                    source=EventSource.PAGE_OBSERVER,
                    timestamp=ts + timedelta(milliseconds=600),
                    sequence_no=seq,
                    target={"tag": "button", "test_id": "apply-btn", "text": "Apply"},
                    payload={},
                )
            )
            seq += 1

            for noise_idx in range(88):
                events.append(
                    RawEvent(
                        project_id=project.id,
                        session_id=session.id,
                        event_type=(
                            EventType.DOM_MUTATION if noise_idx % 2 == 0 else EventType.RESPONSE
                        ),
                        source=EventSource.PAGE_OBSERVER
                        if noise_idx % 2 == 0
                        else EventSource.PLAYWRIGHT,
                        timestamp=ts + timedelta(milliseconds=650 + noise_idx),
                        sequence_no=seq,
                        payload=(
                            {"added_count": 1}
                            if noise_idx % 2 == 0
                            else {"url": "https://perf.example.com/api/ping", "status": 200}
                        ),
                    )
                )
                seq += 1

        assert len(events) == 5000

        t0 = time.perf_counter()
        inserted = ctx.event_repository.insert_batch(events)
        assert inserted == 5000

        loaded = ctx.event_repository.list_by_session(session.id)
        assert len(loaded) == 5000

        processed = ProcessingEngine().process(session.id, loaded).processed_events
        scenarios = ScenarioDetector().detect(project.id, session.id, processed)
        definitions = TestDefinitionBuilder().build(
            project_id=project.id,
            session_id=session.id,
            scenarios=scenarios,
            timeline=processed,
            include_journey=True,
            base_url=project.base_url,
        )
        suite = PytestPlaywrightGenerator().generate_suite(definitions, base_url=project.base_url)
        elapsed = time.perf_counter() - t0

        # 5,000 raw events compressed into meaningful actions and scenarios well under 5 seconds
        assert 50 <= len(processed) <= 200
        assert len(scenarios) >= 1
        assert len(definitions) >= 2
        assert len(suite.test_files) >= 1
        assert elapsed < 5.0, f"5,000-event pipeline took too long: {elapsed:.2f}s"
    finally:
        ctx.close()


def test_environment_diagnostics_report(tmp_settings: AppSettings) -> None:
    report = run_environment_diagnostics(tmp_settings, check_browser=False)
    assert report.ok is True
    text = report.format_text()
    assert "AutoTest Environment Doctor" in text
    assert "Status: READY" in text
    assert "[PASS] Python Runtime" in text
    assert "[PASS] SQLite Engine" in text
