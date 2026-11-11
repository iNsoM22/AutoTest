"""Tests for §10.4 artifacts: recording video, failure traces, export/import, PDF, and replay."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.app import AppContext, create_app_context
from src.browser import BrowserLaunchConfig, BrowserManager
from src.browser.reproducer import ProcessedRecordingReproducer
from src.config import AppSettings
from src.definitions import (
    AssertionType,
    TestAction,
    TestAssertion,
    TestDefinition,
    TestMode,
)
from src.execution import ExecutionMode, RunStatus, TestResultStatus
from src.export import (
    RecordingBundleError,
    export_recording_pdf,
    render_recording_report_html,
)
from src.processing import (
    CorrelatedOutcome,
    OutcomeType,
    ProcessedActionType,
    ProcessedEvent,
)
from src.projects import ProjectCreate
from src.scenarios import Scenario


@pytest.fixture
def app_context(tmp_settings: AppSettings) -> Iterator[AppContext]:
    """Provide an isolated AppContext backed by temporary SQLite & artifacts."""
    ctx = create_app_context(tmp_settings)
    try:
        yield ctx
    finally:
        ctx.close()


def _seed_processed_session(app_context: AppContext, start_url: str) -> tuple[str, str]:
    project = app_context.project_service.create_project(
        ProjectCreate(name="Demo Portal", base_url=start_url)
    )
    session = app_context.session_service.create_session(
        project.id, name="Login & Add Task", start_url=start_url
    )
    now = datetime.now(UTC)
    e1 = ProcessedEvent(
        project_id=project.id,
        session_id=session.id,
        order_index=1,
        action_type=ProcessedActionType.NAVIGATE,
        summary=f"Navigate to {start_url}",
        payload={"url": start_url},
        started_at=now,
        ended_at=now,
    )
    e2 = ProcessedEvent(
        project_id=project.id,
        session_id=session.id,
        order_index=2,
        action_type=ProcessedActionType.FILL,
        summary="Fill username",
        target={"tag": "input", "test_id": "username", "test_id_attr": "data-testid"},
        payload={"value": "alice", "url": start_url},
        started_at=now,
        ended_at=now,
    )
    e3 = ProcessedEvent(
        project_id=project.id,
        session_id=session.id,
        order_index=3,
        action_type=ProcessedActionType.CLICK,
        summary="Click Submit",
        target={"tag": "button", "test_id": "submit-btn", "test_id_attr": "data-testid"},
        payload={"url": start_url},
        started_at=now,
        ended_at=now,
        outcomes=[
            CorrelatedOutcome(
                outcome_type=OutcomeType.DOM_CHANGE,
                summary="Greeting 'Welcome, alice!' appeared",
                timestamp=now,
            )
        ],
    )
    app_context.processed_event_repository.replace_for_session(session.id, [e1, e2, e3])
    scenario = Scenario(
        project_id=project.id,
        session_id=session.id,
        name="User Login",
        description="Sign in with a username <script>alert(1)</script>",
        order_index=1,
        included=True,
        processed_event_ids=[e1.id, e2.id, e3.id],
    )
    app_context.scenario_service._scenario_repo.replace_for_session(session.id, [scenario])
    return project.id, session.id


def test_processed_recording_export_import_and_pdf(
    app_context: AppContext,
    tmp_path: Path,
    qtbot: object,
) -> None:
    """Exporting a processed session to JSON & PDF and importing it recreates everything."""
    _ = qtbot  # Ensures QApplication exists for Qt QPrinter PDF export.
    _proj_id, session_id = _seed_processed_session(app_context, "https://example.test/app")

    bundle_path = tmp_path / "login_flow.json"
    bundle = app_context.recording_bundle_service.export_to_file(session_id, bundle_path)
    assert bundle_path.is_file()
    assert len(bundle.processed_events) == 3
    assert len(bundle.scenarios) == 1

    # Import into a brand new project and verify IDs are remapped cleanly.
    target_project = app_context.project_service.create_project(
        ProjectCreate(name="Imported Target", base_url="https://example.test")
    )
    imported_session = app_context.recording_bundle_service.import_from_file(
        target_project.id, bundle_path
    )
    assert imported_session.project_id == target_project.id
    assert imported_session.id != session_id
    assert imported_session.metadata.get("imported") is True

    imported_events = app_context.processed_event_repository.list_by_session(imported_session.id)
    imported_scenarios = app_context.scenario_service.list_scenarios(imported_session.id)
    assert len(imported_events) == 3
    assert len(imported_scenarios) == 1
    assert imported_scenarios[0].processed_event_ids == [ev.id for ev in imported_events]

    # Generated test definitions work seamlessly on the imported session.
    defs = app_context.definition_service.generate_from_session(imported_session.id)
    assert len(defs) >= 1

    # HTML escaping & PDF generation produce a valid PDF file.
    html_report = render_recording_report_html(bundle)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_report
    assert "User Login" in html_report
    pdf_path = tmp_path / "login_report.pdf"
    export_recording_pdf(bundle, pdf_path)
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    # Invalid bundle raises RecordingBundleError.
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecordingBundleError):
        app_context.recording_bundle_service.import_from_file(target_project.id, bad_file)


@pytest.mark.browser
def test_recording_video_capture_and_live_reproducer(
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    """Chromium records a .webm video and Reproducer replays processed steps accurately."""
    page_file = tmp_path / "demo.html"
    page_file.write_text(
        """<!DOCTYPE html>
<html><body>
  <input data-testid="username" type="text" />
  <button
    data-testid="submit-btn"
    onclick="document.getElementById('out').textContent =
      'Hi ' + document.querySelector('[data-testid=username]').value"
  >Go</button>
  <div id="out" data-testid="greeting"></div>
</body></html>
""",
        encoding="utf-8",
    )
    page_url = page_file.as_uri()

    # 1. Verify Playwright video capture via BrowserManager
    video_dir = tmp_path / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    manager = BrowserManager()
    manager.launch(
        BrowserLaunchConfig(
            headless=True,
            start_url=page_url,
            record_video_dir=str(video_dir),
        )
    )
    manager.poll_events(150)
    manager.close()
    webm_files = list(video_dir.glob("*.webm"))
    assert len(webm_files) >= 1
    assert webm_files[0].stat().st_size > 0

    # 2. Verify live ProcessedRecordingReproducer replays the steps cleanly
    _proj_id, session_id = _seed_processed_session(app_context, page_url)
    events = app_context.processed_event_repository.list_by_session(session_id)
    reproducer = ProcessedRecordingReproducer()
    summary = reproducer.reproduce(
        session_id,
        events,
        start_url=page_url,
        headless=True,
        slow_mo_ms=0,
        hold_open_ms=0,
    )
    assert summary.total_steps == 3
    assert summary.passed_steps == 3
    assert summary.failed_steps == 0
    assert not summary.cancelled


@pytest.mark.browser
def test_failure_captures_playwright_trace_zip(
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    """A failing executed test captures and registers a valid Playwright trace .zip artifact."""
    page_file = tmp_path / "fail_page.html"
    page_file.write_text(
        "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>", encoding="utf-8"
    )
    page_url = page_file.as_uri()

    project = app_context.project_service.create_project(
        ProjectCreate(name="Trace Test", base_url=page_url)
    )
    session = app_context.session_service.create_session(
        project.id, name="Trace Session", start_url=page_url
    )
    failing_def = TestDefinition(
        project_id=project.id,
        session_id=session.id,
        name="Intentional Failure",
        function_name="test_intentional_failure",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        included=True,
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.NAVIGATE,
                summary=f"Navigate to {page_url}",
                value=page_url,
            )
        ],
        assertions=[
            TestAssertion(
                order_index=1,
                assertion_type=AssertionType.URL_CONTAINS,
                expected_value="nonexistent-expected-path",
                description="Expect missing URL segment",
                enabled=True,
            )
        ],
    )
    failing_def.settings.timeout_ms = 1500
    app_context.definition_service._definition_repo.replace_for_session(session.id, [failing_def])

    run, results = app_context.execution_service.execute_definitions(
        session.id,
        [failing_def.id],
        ExecutionMode.INDEPENDENT,
        headed=False,
    )
    assert run.status == RunStatus.FAILED
    assert len(results) == 1
    assert results[0].status == TestResultStatus.FAILED
    assert results[0].screenshot_artifact_id is not None
    assert results[0].trace_artifact_id is not None

    trace_path = app_context.execution_service.get_artifact_path(results[0].trace_artifact_id)
    assert trace_path is not None
    assert trace_path.is_file()
    assert zipfile.is_zipfile(trace_path)
