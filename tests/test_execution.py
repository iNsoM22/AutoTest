"""Tests for built-in Pytest execution runner, persistence, history, and reruns."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.config import AppSettings
from src.definitions import (
    AssertionType,
    SelectorCandidate,
    SelectorStrategy,
    TestAction,
    TestAssertion,
    TestDefinition,
    TestDefinitionRepository,
    TestExecutionSettings,
    TestMode,
    TestPrerequisite,
)
from src.execution import (
    ExecutionError,
    ExecutionMode,
    ExecutionProgressEvent,
    ExecutionRepository,
    ExecutionService,
    RunStatus,
    TestCaseResult,
    TestResultStatus,
    TestRun,
)
from src.processing import ProcessedActionType
from src.projects import (
    ProjectCreate,
    ProjectRepository,
    ProjectService,
    SessionRepository,
    SessionService,
)
from src.storage import ArtifactManager, ArtifactRepository, Database


def _setup_execution_stack(
    tmp_settings: AppSettings,
) -> tuple[
    Database,
    ProjectService,
    SessionService,
    TestDefinitionRepository,
    ExecutionRepository,
    ExecutionService,
    ArtifactManager,
]:
    db = Database(tmp_settings.db_path)
    db.initialize()
    artifacts = ArtifactManager(tmp_settings.artifacts_dir)
    artifacts.ensure_base_dir()
    artifact_repo = ArtifactRepository(db)
    project_repo = ProjectRepository(db)
    session_repo = SessionRepository(db)
    def_repo = TestDefinitionRepository(db)
    exec_repo = ExecutionRepository(db)
    project_service = ProjectService(project_repo, artifacts)
    session_service = SessionService(session_repo, project_repo, artifacts)
    exec_service = ExecutionService(
        project_repo,
        session_repo,
        def_repo,
        exec_repo,
        artifacts,
        artifact_repo,
    )
    return db, project_service, session_service, def_repo, exec_repo, exec_service, artifacts


def test_execution_repository_crud_and_cascade(tmp_settings: AppSettings) -> None:
    db, project_service, session_service, _def_repo, exec_repo, _exec_service, _artifacts = (
        _setup_execution_stack(tmp_settings)
    )
    try:
        project = project_service.create_project(ProjectCreate(name="ExecRepoApp"))
        session = session_service.create_session(project.id, name="S1")

        run = TestRun(
            project_id=project.id,
            session_id=session.id,
            mode=ExecutionMode.INDEPENDENT,
            status=RunStatus.RUNNING,
            total_count=2,
        )
        exec_repo.create_run(run)

        r1 = TestCaseResult(
            run_id=run.id,
            function_name="test_one",
            test_name="One",
            status=TestResultStatus.PENDING,
            order_index=1,
        )
        r2 = TestCaseResult(
            run_id=run.id,
            function_name="test_two",
            test_name="Two",
            status=TestResultStatus.PENDING,
            order_index=2,
        )
        exec_repo.create_results([r1, r2])

        r1.status = TestResultStatus.PASSED
        r1.duration_ms = 120
        exec_repo.update_result(r1)

        run.status = RunStatus.PASSED
        run.passed_count = 1
        exec_repo.update_run(run)

        fetched = exec_repo.get_run(run.id)
        assert fetched is not None
        assert fetched.status == RunStatus.PASSED
        assert fetched.passed_count == 1
        assert len(exec_repo.list_runs_by_session(session.id)) == 1
        assert len(exec_repo.list_runs_by_project(project.id)) == 1

        results = exec_repo.list_results_by_run(run.id)
        assert [r.function_name for r in results] == ["test_one", "test_two"]
        assert results[0].status == TestResultStatus.PASSED
        assert results[0].duration_ms == 120

        assert exec_repo.delete_run(run.id) is True
        assert exec_repo.get_run(run.id) is None
        assert exec_repo.list_results_by_run(run.id) == []
    finally:
        db.close_all()


@pytest.mark.browser
def test_subprocess_execution_pass_fail_screenshot_console_and_rerun(
    tmp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    html_file = tmp_path / "fixture_page.html"
    html_file.write_text(
        """<!doctype html>
<html>
  <head><title>AutoTest Runner Fixture</title></head>
  <body>
    <h1 id="title">Welcome Runner</h1>
    <button
      data-testid="go-btn"
      onclick="document.getElementById('msg').textContent = 'Clicked OK';"
    >
      Go
    </button>
    <button data-testid="err-btn" onclick="console.error('Simulated console boom');">
      Trigger Console Error
    </button>
    <div id="msg">Initial</div>
  </body>
</html>
""",
        encoding="utf-8",
    )
    page_url = html_file.as_uri()

    db, project_service, session_service, def_repo, _exec_repo, exec_service, _artifacts = (
        _setup_execution_stack(tmp_settings)
    )
    try:
        project = project_service.create_project(ProjectCreate(name="RunnerApp", base_url=page_url))
        session = session_service.create_session(
            project.id, name="RunnerSession", start_url=page_url
        )

        pass_def = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Click Go Succeeds",
            function_name="test_click_go_succeeds",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            prerequisites=[
                TestPrerequisite(name="entry_url", description="Open fixture", setup_url=page_url)
            ],
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.CLICK,
                    summary="Click Go",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="go-btn"),
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected="Clicked OK",
                    description="Message updates to Clicked OK",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=5000),
        )

        fail_def = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Missing Text Fails",
            function_name="test_missing_text_fails",
            mode=TestMode.INDEPENDENT,
            order_index=2,
            prerequisites=[
                TestPrerequisite(name="entry_url", description="Open fixture", setup_url=page_url)
            ],
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.CLICK,
                    summary="Click Trigger Console Error",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="err-btn"),
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected="This Text Never Appears",
                    description="Intentionally failing assertion",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=1500),
        )

        journey_def = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Complete Journey",
            function_name="test_complete_journey",
            mode=TestMode.JOURNEY,
            order_index=3,
            prerequisites=[
                TestPrerequisite(name="entry_url", description="Open fixture", setup_url=page_url)
            ],
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.CLICK,
                    summary="Click Go",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="go-btn"),
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected="Clicked OK",
                    description="Journey verifies Clicked OK",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=5000),
        )

        def_repo.replace_for_session(session.id, [pass_def, fail_def, journey_def])

        events: list[ExecutionProgressEvent] = []
        run, results = exec_service.run_independent(session.id, on_event=events.append)

        assert run.mode == ExecutionMode.INDEPENDENT
        assert run.status == RunStatus.FAILED
        assert run.total_count == 2
        assert run.passed_count == 1
        assert run.failed_count == 1
        assert run.log_artifact_id is not None
        log_path = exec_service.get_artifact_path(run.log_artifact_id)
        assert log_path is not None and log_path.is_file()

        by_func = {r.function_name: r for r in results}
        assert by_func["test_click_go_succeeds"].status == TestResultStatus.PASSED
        assert by_func["test_missing_text_fails"].status == TestResultStatus.FAILED
        assert by_func["test_missing_text_fails"].error_message != ""
        assert by_func["test_missing_text_fails"].traceback != ""
        assert any(
            "Simulated console boom" in err
            for err in by_func["test_missing_text_fails"].console_errors
        )

        # Failure screenshot captured and persisted as an artifact
        ss_id = by_func["test_missing_text_fails"].screenshot_artifact_id
        assert ss_id is not None
        ss_path = exec_service.get_artifact_path(ss_id)
        assert ss_path is not None and ss_path.is_file() and ss_path.stat().st_size > 0

        # Progress events streamed
        event_types = {e.event_type for e in events}
        assert {"run_started", "test_started", "test_finished", "run_finished"}.issubset(
            event_types
        )

        # Rerun failed executes only the failed test
        rerun, rerun_results = exec_service.rerun_failed(run.id)
        assert rerun.total_count == 1
        assert rerun_results[0].function_name == "test_missing_text_fails"
        assert rerun.status == RunStatus.FAILED

        # Journey mode executes the journey definition
        journey_run, journey_results = exec_service.run_journey(session.id)
        assert journey_run.mode == ExecutionMode.JOURNEY
        assert journey_run.status == RunStatus.PASSED
        assert len(journey_results) == 1
        assert journey_results[0].function_name == "test_complete_journey"

        # History contains all 3 runs
        history = exec_service.list_runs_by_session(session.id)
        assert len(history) == 3

        # Rerun failed on a passing run raises ExecutionError
        with pytest.raises(ExecutionError, match="no failed tests"):
            exec_service.rerun_failed(journey_run.id)
    finally:
        db.close_all()


def test_pre_cancelled_execution_marks_cancelled(tmp_settings: AppSettings) -> None:
    db, project_service, session_service, def_repo, _exec_repo, exec_service, _artifacts = (
        _setup_execution_stack(tmp_settings)
    )
    try:
        project = project_service.create_project(ProjectCreate(name="CancelApp"))
        session = session_service.create_session(project.id, name="CancelSession")
        defn = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Quick Test",
            function_name="test_quick",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            actions=[],
            assertions=[],
        )
        def_repo.replace_for_session(session.id, [defn])

        cancel_event = threading.Event()
        cancel_event.set()
        run, _results = exec_service.run_single_test(defn.id, cancel_event=cancel_event)
        assert run.status == RunStatus.CANCELLED
    finally:
        db.close_all()
