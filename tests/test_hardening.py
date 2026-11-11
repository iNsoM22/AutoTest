"""Phase 6 hardening tests: performance, redaction, selector reliability, recovery, diagnostics."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

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


def test_frozen_pytest_dispatch_and_pyinstaller_spec(
    tmp_settings: AppSettings,
    tmp_path,
    monkeypatch,
) -> None:
    import subprocess
    import sys
    from pathlib import Path

    from src.execution.runner import ExecutionRunner
    from src.storage import ArtifactManager, ArtifactRepository, Database

    db = Database(tmp_settings.db_path)
    db.initialize()
    try:
        runner = ExecutionRunner(
            ArtifactManager(tmp_settings.artifacts_dir), ArtifactRepository(db)
        )
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert runner._build_pytest_command(["-q"]) == [sys.executable, "-m", "pytest", "-q"]

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert runner._build_pytest_command(["-q"]) == [sys.executable, "--run-pytest", "-q"]
    finally:
        db.close_all()

    # Verify --run-pytest executes a real pytest test file cleanly in a subprocess
    tiny_test = tmp_path / "test_embedded_ok.py"
    tiny_test.write_text("def test_ok() -> None:\n    assert 1 + 1 == 2\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "src.app", "--run-pytest", str(tiny_test), "-q", "--no-header"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Verify PyInstaller spec bundles page_observer.js and pytest plugin metadata
    spec_path = Path(__file__).resolve().parent.parent / "packaging" / "autotest.spec"
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "page_observer.js" in spec_text
    assert 'copy_metadata("pytest-playwright")' in spec_text
    assert 'copy_metadata("pytest-base-url")' in spec_text
    assert 'collect_submodules("pytest_playwright")' in spec_text


def test_ctrl_modifier_shortcut_maps_to_playwright_control() -> None:
    from src.definitions.models import (
        SelectorCandidate,
        SelectorStrategy,
        TestAction,
        TestDefinition,
        TestMode,
    )
    from src.processing.models import ProcessedActionType, ProcessedEvent
    from src.scenarios.models import Scenario

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = ProcessedEvent(
        project_id="proj-1",
        session_id="sess-1",
        order_index=1,
        action_type=ProcessedActionType.PRESS_KEY,
        started_at=now,
        ended_at=now,
        target={"tag": "input", "test_id": "editor"},
        payload={"key": "a", "ctrl": True, "shift": True},
        summary="Press Ctrl+Shift+a",
        raw_event_ids=["raw-1"],
    )
    scenario = Scenario(
        project_id="proj-1",
        session_id="sess-1",
        name="Shortcut Scenario",
        order_index=1,
        processed_event_ids=[ev.id],
    )
    defs = TestDefinitionBuilder().build(
        project_id="proj-1",
        session_id="sess-1",
        scenarios=[scenario],
        timeline=[ev],
        include_journey=False,
    )
    assert defs[0].actions[0].key == "Control+Shift+a"

    gen = PytestPlaywrightGenerator()
    code = gen.render_test_module(defs)
    assert ".press('Control+Shift+a')" in code

    # Manually edited shorthand "Ctrl+Enter" is also normalized during codegen
    manual_def = TestDefinition(
        project_id="proj-1",
        session_id="sess-1",
        name="Manual Shortcut",
        function_name="test_manual_shortcut",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.PRESS_KEY,
                summary="Press Ctrl+Enter",
                selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="editor"),
                key="Ctrl+Enter",
            )
        ],
    )
    manual_code = gen.render_test_module([manual_def])
    assert ".press('Control+Enter')" in manual_code


def test_redaction_avoids_common_word_false_positives_and_syncs_js() -> None:
    from pathlib import Path

    from src.recording.redaction import is_sensitive_key

    # Benign words containing pin/auth/otp substrings must NOT be falsely redacted
    for benign in ("shipping_address", "author", "desktop_mode", "opinion", "hotspot", "mapping"):
        assert not is_sensitive_key(benign), f"False positive on {benign}"
        _, payload = redact_target_and_payload(
            {"tag": "input", "name": benign, "placeholder": f"Enter {benign}"},
            {"value": "hello-world"},
        )
        assert payload["value"] == "hello-world"
        assert not payload.get("redacted")

    # Actual secret field names must still be redacted
    for secret in ("user_pin", "pin-code", "otp_code", "card_cvv", "x-auth", "user_pwd", "ssn"):
        assert is_sensitive_key(secret), f"Missed secret key {secret}"

    # Verify JS page_observer aligns with aria-label/placeholder and matched test-id attributes
    js_path = Path(__file__).resolve().parent.parent / "src" / "recording" / "page_observer.js"
    js_text = js_path.read_text(encoding="utf-8")
    assert 'el.getAttribute("aria-label")' in js_text
    assert 'el.getAttribute("placeholder")' in js_text
    assert "matchedTestAttr" in js_text
    assert "test_id_attr" in js_text


def test_aria_roles_test_id_attrs_standalone_submit_and_multiline_comments() -> None:
    import ast

    from src.definitions.models import (
        AssertionType,
        SelectorCandidate,
        SelectorStrategy,
        TestAction,
        TestAssertion,
        TestDefinition,
        TestMode,
        TestPrerequisite,
    )
    from src.processing.models import ProcessedActionType, ProcessedEvent
    from src.scenarios.models import Scenario

    # 1. ARIA roles for password / search / number
    pw_primary, _ = build_selector_candidates(
        {"tag": "input", "input_type": "password", "aria_label": "Password"}
    )
    assert pw_primary is not None
    assert pw_primary.strategy == SelectorStrategy.LABEL  # NOT role=textbox

    search_primary, _ = build_selector_candidates(
        {"tag": "input", "input_type": "search", "aria_label": "Find items"}
    )
    assert search_primary is not None
    assert search_primary.strategy == SelectorStrategy.ROLE
    assert search_primary.role == "searchbox"

    num_primary, _ = build_selector_candidates(
        {"tag": "input", "input_type": "number", "aria_label": "Quantity"}
    )
    assert num_primary is not None
    assert num_primary.strategy == SelectorStrategy.ROLE
    assert num_primary.role == "spinbutton"

    # 2. data-test-id and data-cy generate CSS attribute selectors instead of get_by_test_id
    cy_primary, _ = build_selector_candidates(
        {"tag": "button", "test_id": "checkout-btn", "test_id_attr": "data-cy"}
    )
    assert cy_primary is not None
    assert cy_primary.strategy == SelectorStrategy.CSS
    assert cy_primary.value == '[data-cy="checkout-btn"]'

    dt_primary, _ = build_selector_candidates(
        {"tag": "button", "test_id": "save-btn", "test_id_attr": "data-test-id"}
    )
    assert dt_primary is not None
    assert dt_primary.strategy == SelectorStrategy.CSS
    assert dt_primary.value == '[data-test-id="save-btn"]'

    # 3. Standalone <form> SUBMIT uses requestSubmit() instead of form.click()
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    submit_ev = ProcessedEvent(
        project_id="proj-1",
        session_id="sess-1",
        order_index=1,
        action_type=ProcessedActionType.SUBMIT,
        started_at=now,
        ended_at=now,
        target={"tag": "form", "id": "login-form"},
        payload={},
        summary="Submit login form\nline two",
        raw_event_ids=["r1"],
    )
    scenario = Scenario(
        project_id="proj-1",
        session_id="sess-1",
        name="Form Submit",
        order_index=1,
        processed_event_ids=[submit_ev.id],
    )
    defs = TestDefinitionBuilder().build(
        project_id="proj-1",
        session_id="sess-1",
        scenarios=[scenario],
        timeline=[submit_ev],
        include_journey=False,
    )
    assert defs[0].actions[0].via_form_submit is True
    gen = PytestPlaywrightGenerator()
    submit_code = gen.render_test_module(defs)
    ast.parse(submit_code)
    assert ".evaluate('form => form.requestSubmit()')" in submit_code
    assert ".click()" not in submit_code

    # 4. Multiline comments never cause SyntaxError
    multiline_def = TestDefinition(
        project_id="proj-1",
        session_id="sess-1",
        name="Multiline Comments",
        function_name="test_multiline_comments",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        prerequisites=[
            TestPrerequisite(
                name="entry_url",
                description="Line 1\nLine 2\r\nLine 3",
                setup_url="https://example.com",
            )
        ],
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.SCROLL,
                summary="Scroll down\nwith newline",
            ),
            TestAction(
                order_index=2,
                action_type=ProcessedActionType.CLICK,
                summary="Click\nmissing selector",
                selector=None,
            ),
        ],
        assertions=[
            TestAssertion(
                order_index=1,
                assertion_type=AssertionType.TEXT_VISIBLE,
                description="Verify heading\nacross lines",
                expected="Welcome",
                selector=SelectorCandidate(strategy=SelectorStrategy.CSS, value="h1"),
            ),
            TestAssertion(
                order_index=2,
                assertion_type=AssertionType.NETWORK_RESPONSE,
                description="Network\ncheck",
                expected="200\n/api/save",
                enabled=True,
            ),
        ],
    )
    multiline_code = gen.render_test_module([multiline_def])
    ast.parse(multiline_code)


def test_phase1_to_3_runtime_redaction_and_correlation_regressions(
    tmp_settings: AppSettings,
) -> None:
    import threading
    from datetime import timedelta

    from src.browser import BrowserLaunchConfig, BrowserManager, BrowserManagerError, BrowserState
    from src.processing import ProcessingConfig, ProcessingEngine
    from src.recording import EventSource, EventType, RawEvent, redact_url
    from src.scenarios import ScenarioError
    from src.storage import Database

    # 1. SQLite worker-thread connection cleanup via Database.close_current_thread_connections()
    db = Database(tmp_settings.db_path)
    db.initialize()
    worker_errors: list[Exception] = []

    def _worker() -> None:
        try:
            db.connect().execute("SELECT 1;").fetchone()
        except Exception as exc:
            worker_errors.append(exc)
        finally:
            Database.close_current_thread_connections()

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=5.0)
    assert not worker_errors
    assert len(db._connections) == 1  # Only main thread remains
    db.close_all()
    assert len(db._connections) == 0

    # 2. BrowserManager preserves BrowserState.ERROR after launch failure
    mgr = BrowserManager()
    with pytest.raises(BrowserManagerError):
        mgr.launch(BrowserLaunchConfig(start_url="http://127.0.0.1:1/unreachable-port-test"))
    assert mgr.state == BrowserState.ERROR

    # 3. Enter keypress on password field is NOT redacted, and URL fragments ARE redacted
    _, key_payload = redact_target_and_payload(
        {"tag": "input", "input_type": "password", "name": "password"},
        {"key": "Enter"},
    )
    assert key_payload["key"] == "Enter"
    assert not key_payload.get("redacted")

    redacted_frag = redact_url("https://app.example.com/cb#access_token=tok123&state=ok")
    assert "tok123" not in redacted_frag
    assert "state=ok" in redacted_frag
    redacted_spa = redact_url("https://app.example.com/#/reset?token=secret99&view=1")
    assert "secret99" not in redacted_spa
    assert "view=1" in redacted_spa

    # 4. Outcome correlation measures from action.ended_at (not started_at)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    raw = [
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.INPUT,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0,
            sequence_no=1,
            target={"tag": "input", "id": "search"},
            payload={"value": "a", "is_trusted": True},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.INPUT,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0 + timedelta(milliseconds=1200),
            sequence_no=2,
            target={"tag": "input", "id": "search"},
            payload={"value": "ab", "is_trusted": True},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.INPUT,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0 + timedelta(milliseconds=2400),
            sequence_no=3,
            target={"tag": "input", "id": "search"},
            payload={"value": "abc", "is_trusted": True},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.DOM_MUTATION,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0 + timedelta(milliseconds=2800),
            sequence_no=4,
            target={"tag": "document"},
            payload={"notable": [{"tag": "div", "id": "results", "text": "3 matches"}]},
        ),
    ]
    proc_result = ProcessingEngine(
        ProcessingConfig(input_merge_window_ms=1500, correlation_window_ms=1000)
    ).process("s1", raw)
    assert len(proc_result.processed_events) == 1
    assert len(proc_result.processed_events[0].outcomes) == 1
    assert not proc_result.uncorrelated_outcomes

    # 5. Non-adjacent merge_scenarios raises ScenarioError
    ctx = create_app_context(tmp_settings)
    try:
        project = ctx.project_service.create_project(ProjectCreate(name="MergeApp"))
        session = ctx.session_service.create_session(project.id, name="MergeSession")
        from src.scenarios import Scenario, ScenarioRepository

        s1 = Scenario(project_id=project.id, session_id=session.id, name="S1", order_index=1)
        s2 = Scenario(project_id=project.id, session_id=session.id, name="S2", order_index=2)
        s3 = Scenario(project_id=project.id, session_id=session.id, name="S3", order_index=3)
        ScenarioRepository(ctx.database).replace_for_session(session.id, [s1, s2, s3])
        with pytest.raises(ScenarioError, match="adjacent"):
            ctx.scenario_service.merge_scenarios([s1.id, s3.id])
    finally:
        ctx.close()


def test_auth_heuristic_selector_codegen_and_artifact_cleanup(
    tmp_settings: AppSettings,
) -> None:
    import ast

    from src.definitions.builder import parse_selector_expression
    from src.definitions.models import (
        AssertionType,
        SelectorStrategy,
        TestAction,
        TestAssertion,
        TestDefinition,
        TestMode,
    )
    from src.processing.models import ProcessedActionType, ProcessedEvent
    from src.scenarios.models import Scenario

    # 1. Payment CVV redaction must NOT trigger authenticated_page on subsequent scenarios
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev1 = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=1,
        action_type=ProcessedActionType.FILL,
        started_at=now,
        ended_at=now,
        target={"tag": "input", "name": "card_cvv", "autocomplete": "cc-csc"},
        payload={"value": "[REDACTED]", "redacted": True},
        summary="Fill CVV",
        raw_event_ids=["r1"],
    )
    ev2 = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=2,
        action_type=ProcessedActionType.CLICK,
        started_at=now,
        ended_at=now,
        target={"tag": "button", "test_id": "receipt-btn"},
        payload={},
        summary="View receipt",
        raw_event_ids=["r2"],
    )
    sc1 = Scenario(
        project_id="p1",
        session_id="s1",
        name="Checkout Payment",
        order_index=1,
        processed_event_ids=[ev1.id],
    )
    sc2 = Scenario(
        project_id="p1",
        session_id="s1",
        name="View Receipt",
        order_index=2,
        processed_event_ids=[ev2.id],
    )
    defs = TestDefinitionBuilder().build(
        project_id="p1",
        session_id="s1",
        scenarios=[sc1, sc2],
        timeline=[ev1, ev2],
        include_journey=False,
    )
    assert all(p.fixture_name != "authenticated_page" for p in defs[1].prerequisites)

    # 2. Selector & codegen edge cases
    with pytest.raises(ValueError):
        parse_selector_expression("role=")
    parsed_role = parse_selector_expression("role=button[name='Save \"All\"']")
    assert parsed_role.role == "button"
    assert parsed_role.name == 'Save "All"'

    alt_primary, _ = build_selector_candidates({"tag": "input", "alt": "Submit\nNow"})
    assert alt_primary is not None
    assert alt_primary.strategy == SelectorStrategy.CSS
    assert alt_primary.value.startswith('input[alt="Submit\\00000aNow"]')

    gen = PytestPlaywrightGenerator()
    # Duplicate function names deduplicated, OPEN_TAB/CLOSE_TAB/SCROLL codegen, no unused expect
    d1 = TestDefinition(
        project_id="p1",
        session_id="s1",
        scenario_id="sc-99",
        name="Dup Flow",
        function_name="test_dup_flow",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.OPEN_TAB,
                summary="Open tab",
                url="https://example.com/tab",
            ),
            TestAction(
                order_index=2,
                action_type=ProcessedActionType.CLOSE_TAB,
                summary="Close tab",
            ),
            TestAction(
                order_index=3,
                action_type=ProcessedActionType.SCROLL,
                summary="Scroll to (0, 250)",
                value="0,250",
            ),
        ],
        assertions=[
            TestAssertion(
                order_index=1,
                assertion_type=AssertionType.ELEMENT_CHECKED,
                description="Missing selector check",
                selector=None,
                expected="true",
            ),
            TestAssertion(
                order_index=2,
                assertion_type=AssertionType.ELEMENT_VALUE,
                description="Missing selector value",
                selector=None,
                expected="abc",
            ),
        ],
        raw_event_ids=["raw-a", "raw-b"],
    )
    d2 = d1.model_copy(update={"id": "def-dup-2", "order_index": 2})
    mod = gen.render_test_module([d1, d2])
    ast.parse(mod)
    assert "def test_dup_flow(" in mod
    assert "def test_dup_flow_2(" in mod
    assert "expect" not in mod.splitlines()[4]  # No unused expect import
    assert "new_page = page.context.new_page()" in mod
    assert "new_page.close()" in mod
    assert "window.scrollTo(0, 250)" in mod
    assert "Scenario ID: sc-99" in mod
    assert "raw-a, raw-b" in mod
    assert mod.count("# TODO: missing selector") >= 2

    ini = gen.render_pytest_ini(base_url="https://example.com/%20path")
    assert "%%20path" in ini.content
    conftest = gen.render_conftest([d1], base_url="https://example.com")
    assert 'pytestconfig.getoption("base_url"' in conftest.content

    # 3. Deleting project or session removes artifact directories on disk
    ctx = create_app_context(tmp_settings)
    try:
        proj = ctx.project_service.create_project(ProjectCreate(name="CleanupProj"))
        sess = ctx.session_service.create_session(proj.id, name="CleanupSess")
        sess_dir = ctx.artifacts.session_dir(proj.id, sess.id)
        proj_dir = ctx.artifacts.project_dir(proj.id)
        assert sess_dir.is_dir()
        ctx.session_service.delete_session(sess.id)
        assert not sess_dir.exists()
        ctx.project_service.delete_project(proj.id)
        assert not proj_dir.exists()
    finally:
        ctx.close()


def test_round2_critical_and_high_regressions(
    tmp_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Critical #1-2 and High #3-15 fixes end-to-end."""
    import ast
    import logging
    import sys
    from datetime import timedelta
    from pathlib import Path

    from src.config.logging import configure_logging
    from src.definitions.models import (
        AssertionType,
        SelectorCandidate,
        SelectorStrategy,
        TestAction,
        TestAssertion,
        TestDefinition,
        TestMode,
        TestPrerequisite,
    )
    from src.definitions.repository import TestDefinitionRepository
    from src.execution.models import ExecutionMode, RunStatus, TestResultStatus
    from src.processing import ProcessingEngine
    from src.processing.models import OutcomeType, ProcessedActionType
    from src.recording.models import EventSource, EventType, RawEvent
    from src.recording.page_observer import _ALLOWED_EVENT_TYPES
    from src.scenarios import Scenario, ScenarioRepository

    # 1. Critical #1: Scenario reorder/split/merge/delete preserve surviving definition FKs
    ctx = create_app_context(tmp_settings)
    try:
        project = ctx.project_service.create_project(ProjectCreate(name="FKApp"))
        session = ctx.session_service.create_session(project.id, name="FKSession")
        sc_repo = ScenarioRepository(ctx.database)
        def_repo = TestDefinitionRepository(ctx.database)

        s1 = Scenario(
            project_id=project.id,
            session_id=session.id,
            name="S1",
            order_index=1,
            processed_event_ids=["e1", "e2"],
        )
        s2 = Scenario(
            project_id=project.id,
            session_id=session.id,
            name="S2",
            order_index=2,
            processed_event_ids=["e3"],
        )
        s3 = Scenario(
            project_id=project.id,
            session_id=session.id,
            name="S3",
            order_index=3,
            processed_event_ids=["e4"],
        )
        sc_repo.replace_for_session(session.id, [s1, s2, s3])

        d1 = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            scenario_id=s1.id,
            name="Def 1",
            function_name="test_def_1",
            order_index=1,
        )
        d2 = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            scenario_id=s2.id,
            name="Def 2",
            function_name="test_def_2",
            order_index=2,
        )
        d3 = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            scenario_id=s3.id,
            name="Def 3",
            function_name="test_def_3",
            order_index=3,
        )
        def_repo.replace_for_session(session.id, [d1, d2, d3])

        # Reorder must preserve all FKs
        ctx.scenario_service.reorder_scenarios(session.id, [s2.id, s1.id, s3.id])
        assert def_repo.get(d1.id).scenario_id == s1.id  # type: ignore[union-attr]
        assert def_repo.get(d2.id).scenario_id == s2.id  # type: ignore[union-attr]
        assert def_repo.get(d3.id).scenario_id == s3.id  # type: ignore[union-attr]

        # Split must preserve all FKs
        ctx.scenario_service.split_scenario(s1.id, 1)
        assert def_repo.get(d1.id).scenario_id == s1.id  # type: ignore[union-attr]
        assert def_repo.get(d2.id).scenario_id == s2.id  # type: ignore[union-attr]
        assert def_repo.get(d3.id).scenario_id == s3.id  # type: ignore[union-attr]

        # Delete s3 must preserve d1 and d2 FKs while nulling only d3
        ctx.scenario_service.delete_scenario(s3.id)
        assert def_repo.get(d1.id).scenario_id == s1.id  # type: ignore[union-attr]
        assert def_repo.get(d2.id).scenario_id == s2.id  # type: ignore[union-attr]
        assert def_repo.get(d3.id).scenario_id is None  # type: ignore[union-attr]

        # 2. Critical #2: Duplicate & cross-module function names all execute & map cleanly
        dup_a = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Dup Same",
            function_name="test_dup_same",
            mode=TestMode.INDEPENDENT,
            order_index=1,
        )
        dup_b = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Dup Same Copy",
            function_name="test_dup_same",
            mode=TestMode.INDEPENDENT,
            order_index=2,
        )
        dup_j = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Dup Same Journey",
            function_name="test_dup_same",
            mode=TestMode.JOURNEY,
            order_index=3,
        )
        def_repo.replace_for_session(session.id, [dup_a, dup_b, dup_j])
        run, results = ctx.execution_service.execute_definitions(
            session.id,
            [dup_a.id, dup_b.id, dup_j.id],
            ExecutionMode.INDEPENDENT,
        )
        assert run.status == RunStatus.PASSED
        assert run.passed_count == 3
        assert all(r.status == TestResultStatus.PASSED for r in results)
        assert [r.function_name for r in results] == [
            "test_dup_same",
            "test_dup_same_2",
            "test_dup_same",
        ]
    finally:
        ctx.close()

    # 3. High #3 & #5: PageObserver allows NAVIGATION; ProcessingEngine preserves reload,
    # ignores tab_switch, and deduplicates click-triggered navigation outcomes
    assert EventType.NAVIGATION in _ALLOWED_EVENT_TYPES
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    nav_events = [
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.NAVIGATION,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0,
            sequence_no=1,
            payload={"url": "https://example.com/a", "navigation_type": "navigate"},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.NAVIGATION,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=300),
            sequence_no=2,
            payload={"url": "https://example.com/a", "navigation_type": "reload"},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.NAVIGATION,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0 + timedelta(milliseconds=400),
            sequence_no=3,
            payload={"url": "https://example.com/a", "navigation_type": "tab_switch"},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.CLICK,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0 + timedelta(milliseconds=500),
            sequence_no=4,
            target={"tag": "a", "text": "Go B"},
            payload={},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.NAVIGATION,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=600),
            sequence_no=5,
            payload={"url": "https://example.com/b", "navigation_type": "navigate"},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.NAVIGATION,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=650),
            sequence_no=6,
            payload={"url": "https://example.com/b", "title": "Page B"},
        ),
    ]
    proc = ProcessingEngine().process("s1", nav_events)
    assert len(proc.processed_events) == 3  # navigate + reload + click
    click_outcomes = [
        o for o in proc.processed_events[2].outcomes if o.outcome_type == OutcomeType.NAVIGATION
    ]
    assert len(click_outcomes) == 1
    assert click_outcomes[0].details["title"] == "Page B"

    # 4. High #9, #10, #11, #13: Multi-tab stack, auth setup_url guard, docstring escaping,
    # and subset auth_context_definitions
    gen = PytestPlaywrightGenerator()
    tab_def = TestDefinition(
        project_id="p1",
        session_id="s1",
        name=r'Flow C:\Users\demo "Quoted"',
        description=r"Handles C:\temp\file",
        function_name="test_tabs_and_docs",
        mode=TestMode.INDEPENDENT,
        order_index=2,
        prerequisites=[
            TestPrerequisite(
                name="entry_url",
                description="Start on app",
                setup_url="https://example.com/app",
            ),
            TestPrerequisite(
                name="authenticated_session",
                description="Use authenticated session",
                fixture_name="authenticated_page",
            ),
        ],
        actions=[
            TestAction(
                id="act-open",
                order_index=1,
                action_type=ProcessedActionType.OPEN_TAB,
                summary="Open tab",
                url="https://example.com/popup",
            ),
            TestAction(
                id="act-click-new",
                order_index=2,
                action_type=ProcessedActionType.CLICK,
                summary="Click popup button",
                selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="popup-btn"),
            ),
            TestAction(
                id="act-close",
                order_index=3,
                action_type=ProcessedActionType.CLOSE_TAB,
                summary="Close tab",
            ),
            TestAction(
                id="act-click-orig",
                order_index=4,
                action_type=ProcessedActionType.CLICK,
                summary="Click main button",
                selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="main-btn"),
            ),
        ],
        assertions=[
            TestAssertion(
                order_index=1,
                after_action_id="act-click-new",
                assertion_type=AssertionType.TEXT_VISIBLE,
                description="Popup text",
                expected="Saved",
            )
        ],
    )
    login_def = TestDefinition(
        project_id="p1",
        session_id="s1",
        name="User Login",
        function_name="test_user_login",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.CLICK,
                summary="Sign in",
                selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="login-btn"),
            )
        ],
    )
    mod_src = gen.render_test_module([tab_def], module_doc='Suite for "Checkout"')
    ast.parse(mod_src)
    assert "if authenticated_page.url.rstrip('/') != 'https://example.com/app':" in mod_src
    assert "new_page.get_by_test_id('popup-btn').click()" in mod_src
    assert "expect(new_page.get_by_text('Saved')).to_be_visible()" in mod_src
    assert "new_page.close()" in mod_src
    assert "authenticated_page.get_by_test_id('main-btn').click()" in mod_src

    subset_conftest = gen.render_conftest(
        [tab_def],
        auth_context_definitions=[login_def, tab_def],
    )
    ast.parse(subset_conftest.content)
    assert "def _perform_auth_setup(page: Page) -> None:" in subset_conftest.content

    # 5. High #7 & #8: configure_logging tolerates sys.stderr=None; spec bundles pytest_base_url
    monkeypatch.setattr(sys, "stderr", None)
    configured = configure_logging(tmp_settings)
    assert all(
        not isinstance(h, logging.StreamHandler) or hasattr(h, "baseFilename")
        for h in configured.handlers
    )
    spec_text = (Path(__file__).resolve().parent.parent / "packaging" / "autotest.spec").read_text(
        encoding="utf-8"
    )
    assert 'collect_submodules("pytest_base_url")' in spec_text


def test_round2_medium_and_low_regressions(tmp_settings: AppSettings) -> None:
    """Verify Medium (#19-37) and Low (#38-47) fixes across all subsystems."""
    import ast
    from datetime import timedelta

    from src.definitions.builder import parse_selector_expression
    from src.definitions.models import (
        AssertionType,
        SelectorCandidate,
        SelectorStrategy,
        TestAction,
        TestAssertion,
        TestDefinition,
        TestExecutionSettings,
        TestMode,
    )
    from src.processing import ProcessingEngine
    from src.processing.models import (
        CorrelatedOutcome,
        OutcomeType,
        ProcessedActionType,
        ProcessedEvent,
    )
    from src.projects.exceptions import SessionStateError
    from src.recording.collector import EventCollector
    from src.recording.models import EventSource, EventType, RawEvent
    from src.recording.redaction import redact_target_and_payload
    from src.scenarios.detector import ScenarioDetector
    from src.utils import target_label

    # #39: target_label skips whitespace-only values
    assert target_label({"text": "   ", "aria_label": "Save"}) == "Save"

    # #23: sensitive <select> redacts value and selected_text
    _, clean_sel = redact_target_and_payload(
        {"tag": "select", "name": "card_type", "autocomplete": "cc-type"},
        {"value": "4111", "selected_text": "Visa 4111"},
    )
    assert clean_sel["value"] == "[REDACTED]"
    assert clean_sel["selected_text"] == "[REDACTED]"
    assert clean_sel["redacted"] is True

    # #25 & #26: REQUEST_FAILED static/aborted filtered; slow API correlates by REQUEST timestamp
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    net_raw = [
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.CLICK,
            source=EventSource.PAGE_OBSERVER,
            timestamp=t0,
            sequence_no=1,
            target={"tag": "button", "text": "Load"},
            payload={},
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.REQUEST,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=100),
            sequence_no=2,
            correlation_id="req-slow",
            payload={
                "method": "GET",
                "url": "https://api.example.com/slow",
                "resource_type": "fetch",
            },
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.REQUEST_FAILED,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=150),
            sequence_no=3,
            payload={
                "method": "GET",
                "url": "https://example.com/logo.png",
                "resource_type": "image",
                "failure": "net::ERR_ABORTED",
            },
        ),
        RawEvent(
            project_id="p1",
            session_id="s1",
            page_id="pg1",
            event_type=EventType.RESPONSE,
            source=EventSource.PLAYWRIGHT,
            timestamp=t0 + timedelta(milliseconds=4000),
            sequence_no=4,
            correlation_id="req-slow",
            payload={
                "method": "GET",
                "url": "https://api.example.com/slow",
                "status": 200,
                "resource_type": "fetch",
            },
        ),
    ]
    net_res = ProcessingEngine().process("s1", net_raw)
    assert len(net_res.processed_events) == 1
    assert len(net_res.processed_events[0].outcomes) == 1
    assert not net_res.uncorrelated_outcomes

    # #27: ScenarioDetector updates current_path after navigation outcomes
    ev_a = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=1,
        action_type=ProcessedActionType.NAVIGATE,
        started_at=t0,
        ended_at=t0,
        payload={"url": "https://example.com/login"},
        summary="Navigate to /login",
    )
    ev_b = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=2,
        action_type=ProcessedActionType.CLICK,
        started_at=t0 + timedelta(seconds=1),
        ended_at=t0 + timedelta(seconds=1),
        target={"tag": "button", "text": "Go"},
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.NAVIGATION,
                summary="Navigated to /dashboard",
                details={"url": "https://example.com/dashboard"},
                timestamp=t0 + timedelta(seconds=1),
            )
        ],
        summary="Click Go",
    )
    ev_c = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=3,
        action_type=ProcessedActionType.CLICK,
        started_at=t0 + timedelta(seconds=2),
        ended_at=t0 + timedelta(seconds=2),
        target={"tag": "a", "text": "Back to login"},
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.NAVIGATION,
                summary="Navigated to /login",
                details={"url": "https://example.com/login"},
                timestamp=t0 + timedelta(seconds=2),
            )
        ],
        summary="Click Back to login",
    )
    ev_d = ProcessedEvent(
        project_id="p1",
        session_id="s1",
        order_index=4,
        action_type=ProcessedActionType.CLICK,
        started_at=t0 + timedelta(seconds=3),
        ended_at=t0 + timedelta(seconds=3),
        target={"tag": "button", "text": "Retry"},
        summary="Click Retry",
    )
    detected = ScenarioDetector().detect(
        project_id="p1", session_id="s1", processed_events=[ev_a, ev_b, ev_c, ev_d]
    )
    assert len(detected) == 3

    # #29 & #30: <a> without href has no link role; parse_selector_expression round-trips quotes
    no_href_primary, _ = build_selector_candidates({"tag": "a", "text": "Action"})
    assert no_href_primary is not None
    assert no_href_primary.strategy != SelectorStrategy.ROLE
    with_href_primary, _ = build_selector_candidates({"tag": "a", "href": "/docs", "text": "Docs"})
    assert with_href_primary is not None
    assert with_href_primary.strategy == SelectorStrategy.ROLE
    assert with_href_primary.role == "link"

    tricky_cand = SelectorCandidate(
        strategy=SelectorStrategy.ROLE,
        value="role",
        role="button",
        name='Say "Hi" & don\'t',
    )
    roundtripped = parse_selector_expression(tricky_cand.display())
    assert roundtripped.name == 'Say "Hi" & don\'t'

    # #31, #33, #40: base_url reads getini, per-test timeout/viewport, safe SCROLL parsing
    gen = PytestPlaywrightGenerator()
    t_custom = TestDefinition(
        project_id="p1",
        session_id="s1",
        name="Custom Viewport & Timeout",
        function_name="test_custom_vp",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        settings=TestExecutionSettings(timeout_ms=7500, viewport_width=800, viewport_height=600),
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.SCROLL,
                summary="Bad scroll",
                value="--10,0",
            )
        ],
        assertions=[
            TestAssertion(
                order_index=1,
                assertion_type=AssertionType.TEXT_VISIBLE,
                description="Check text",
                expected="Hello",
            )
        ],
    )
    t_default = TestDefinition(
        project_id="p1",
        session_id="s1",
        name="Default Viewport",
        function_name="test_default_vp",
        mode=TestMode.INDEPENDENT,
        order_index=2,
    )
    suite = gen.generate_suite([t_custom, t_default], base_url="https://example.com")
    ast.parse(suite.conftest.content)
    assert 'pytestconfig.getini("base_url")' in suite.conftest.content
    mod_content = suite.test_files[0].content
    ast.parse(mod_content)
    assert "timeout=7500" in mod_content
    assert "# Bad scroll" in mod_content
    assert "set_viewport_size({'width': 1280, 'height': 720})" in mod_content

    # #20 & #22: EventCollector re-buffers on failure; SessionService closes pages & guards ACTIVE
    class _FailingRepo:
        def insert_batch(self, _batch: list[RawEvent]) -> int:
            raise RuntimeError("transient lock")

    collector = EventCollector(
        _FailingRepo(),  # type: ignore[arg-type]
        project_id="p1",
        session_id="s1",
        batch_size=1,
    )
    with pytest.raises(RuntimeError, match="transient lock"):
        collector.record(event_type=EventType.CLICK, source=EventSource.PAGE_OBSERVER)
    assert collector.buffered_count == 1

    ctx = create_app_context(tmp_settings)
    try:
        proj = ctx.project_service.create_project(ProjectCreate(name="ActiveGuard"))
        sess = ctx.session_service.create_session(proj.id, name="Live")
        ctx.session_service.start_session(sess.id)
        ctx.session_service.register_page(sess.id, page_id="pg-open", tab_index=0, url="https://x")
        with pytest.raises(SessionStateError):
            ctx.session_service.delete_session(sess.id)
        with pytest.raises(SessionStateError):
            ctx.project_service.delete_project(proj.id)
        ctx.session_service.stop_session(sess.id)
        pages = ctx.session_service.list_pages(sess.id)
        assert len(pages) == 1 and pages[0].closed_at is not None
    finally:
        ctx.close()
