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


def test_delete_definition_preserves_other_test_result_fk_links(
    tmp_settings: AppSettings,
) -> None:
    from src.app import create_app_context

    ctx = create_app_context(tmp_settings)
    try:
        project = ctx.project_service.create_project(ProjectCreate(name="FKPreserveApp"))
        session = ctx.session_service.create_session(project.id, name="S1")

        def1 = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="First Test",
            function_name="test_first",
            mode=TestMode.INDEPENDENT,
            order_index=1,
        )
        def2 = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Second Test",
            function_name="test_second",
            mode=TestMode.INDEPENDENT,
            order_index=2,
        )
        ctx.definition_service._definition_repo.replace_for_session(session.id, [def1, def2])

        run = TestRun(
            project_id=project.id,
            session_id=session.id,
            mode=ExecutionMode.INDEPENDENT,
            status=RunStatus.FAILED,
            total_count=2,
            failed_count=2,
        )
        ctx.execution_service._execution_repo.create_run(run)
        ctx.execution_service._execution_repo.create_results(
            [
                TestCaseResult(
                    run_id=run.id,
                    definition_id=def1.id,
                    function_name=def1.function_name,
                    test_name=def1.name,
                    status=TestResultStatus.FAILED,
                    order_index=1,
                ),
                TestCaseResult(
                    run_id=run.id,
                    definition_id=def2.id,
                    function_name=def2.function_name,
                    test_name=def2.name,
                    status=TestResultStatus.FAILED,
                    order_index=2,
                ),
            ]
        )

        # Deleting def1 must NOT nullify def2's historical test_results.definition_id
        ctx.definition_service.delete_definition(def1.id)

        remaining = ctx.definition_service.list_by_session(session.id)
        assert [d.id for d in remaining] == [def2.id]
        assert remaining[0].order_index == 1

        results = ctx.execution_service._execution_repo.list_results_by_run(run.id)
        by_func = {r.function_name: r for r in results}
        assert by_func["test_first"].definition_id is None
        assert by_func["test_second"].definition_id == def2.id
    finally:
        ctx.close()


@pytest.mark.browser
def test_journey_fallback_shares_state_and_authenticated_page_replays_auth(
    tmp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    html_file = tmp_path / "auth_session_fixture.html"
    html_file.write_text(
        """<!doctype html>
<html>
  <head><title>Auth Session Fixture</title></head>
  <body>
    <button
      data-testid="login-btn"
      onclick="window.sessionStorage.setItem('auth_user', 'alice');
               document.getElementById('status').textContent = 'Logged in as alice';"
    >
      Sign In
    </button>
    <button
      data-testid="check-btn"
      onclick="const u = window.sessionStorage.getItem('auth_user') || 'anonymous';
               document.getElementById('session-out').textContent = 'Session: ' + u;"
    >
      Check Session
    </button>
    <div id="status">Signed out</div>
    <div id="session-out">Unknown</div>
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
        project = project_service.create_project(
            ProjectCreate(name="JourneyAuthApp", base_url=page_url)
        )
        session = session_service.create_session(project.id, name="AuthSession", start_url=page_url)

        login_def = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Login Flow",
            function_name="test_login_flow",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            prerequisites=[
                TestPrerequisite(name="entry_url", description="Open app", setup_url=page_url)
            ],
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.CLICK,
                    summary="Click Sign In",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="login-btn"
                    ),
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected="Logged in as alice",
                    description="User is logged in",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=5000),
        )

        protected_def = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Protected Action",
            function_name="test_protected_action",
            mode=TestMode.INDEPENDENT,
            order_index=2,
            prerequisites=[
                TestPrerequisite(
                    name="authenticated_session",
                    description="Requires authenticated state",
                    fixture_name="authenticated_page",
                )
            ],
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.CLICK,
                    summary="Click Check Session",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="check-btn"
                    ),
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected="Session: alice",
                    description="Session state preserved",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=5000),
        )

        # Only INDEPENDENT defs saved (no TestMode.JOURNEY) to test both modes
        def_repo.replace_for_session(session.id, [login_def, protected_def])

        # 1. Journey fallback runs both tests sequentially on one shared browser context/page
        j_run, j_results = exec_service.run_journey(session.id)
        assert j_run.mode == ExecutionMode.JOURNEY
        assert j_run.status == RunStatus.PASSED
        assert [r.status for r in j_results] == [TestResultStatus.PASSED, TestResultStatus.PASSED]

        # 2. Independent mode uses authenticated_page fixture to replay login before protected test
        i_run, i_results = exec_service.run_independent(session.id)
        assert i_run.mode == ExecutionMode.INDEPENDENT
        assert i_run.status == RunStatus.PASSED
        assert [r.status for r in i_results] == [TestResultStatus.PASSED, TestResultStatus.PASSED]
    finally:
        db.close_all()


def test_excluded_definition_and_empty_function_name_execute_cleanly(
    tmp_settings: AppSettings,
) -> None:
    db, project_service, session_service, def_repo, _exec_repo, exec_service, _artifacts = (
        _setup_execution_stack(tmp_settings)
    )
    try:
        project = project_service.create_project(ProjectCreate(name="ExcludedApp"))
        session = session_service.create_session(project.id, name="ExcludedSession")
        defn = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Excluded Empty Func",
            function_name="",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            included=False,
            actions=[],
            assertions=[],
        )
        def_repo.replace_for_session(session.id, [defn])

        run, results = exec_service.run_single_test(defn.id)
        assert run.status == RunStatus.PASSED
        assert len(results) == 1
        assert results[0].status == TestResultStatus.PASSED
        assert results[0].function_name == "test_excluded_empty_func"
    finally:
        db.close_all()


def test_runner_plugin_captures_stdout_stderr_and_teardown_errors(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    from src.execution.runner import _EVENT_PREFIX, _RUNNER_PLUGIN_SOURCE

    plugin_path = tmp_path / "_autotest_runner_plugin.py"
    plugin_path.write_text(_RUNNER_PLUGIN_SOURCE, encoding="utf-8")

    test_file = tmp_path / "test_plugin_capture.py"
    test_file.write_text(
        """import sys
import pytest

@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown boom")

def test_with_output() -> None:
    print("hello-captured-stdout")
    print("hello-captured-stderr", file=sys.stderr)

def test_teardown_failure(broken_teardown) -> None:
    assert True
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "_autotest_runner_plugin",
            "--capture=sys",
            "--tb=short",
            str(test_file),
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    events: dict[str, dict[str, object]] = {}
    for line in proc.stdout.splitlines():
        if _EVENT_PREFIX in line:
            _, payload = line.split(_EVENT_PREFIX, 1)
            data = json.loads(payload)
            if data.get("type") == "test_finished":
                events[str(data["function_name"])] = data

    assert "test_with_output" in events
    assert events["test_with_output"]["status"] == "passed"
    assert "hello-captured-stdout" in str(events["test_with_output"]["stdout"])
    assert "hello-captured-stderr" in str(events["test_with_output"]["stderr"])

    assert "test_teardown_failure" in events
    assert events["test_teardown_failure"]["status"] == "error"
    assert "teardown boom" in str(events["test_teardown_failure"]["traceback"])


def test_execution_service_marks_run_failed_on_runner_exception_and_recovery_counts(
    tmp_settings: AppSettings,
) -> None:
    db, project_service, session_service, def_repo, exec_repo, exec_service, _artifacts = (
        _setup_execution_stack(tmp_settings)
    )
    try:
        project = project_service.create_project(ProjectCreate(name="ErrApp"))
        session = session_service.create_session(project.id, name="ErrSession")
        defn = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Boom Test",
            function_name="test_boom",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            actions=[],
            assertions=[],
        )
        def_repo.replace_for_session(session.id, [defn])

        # Force runner to raise before completion
        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("runner exploded")

        exec_service._runner.execute = _boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="runner exploded"):
            exec_service.run_single_test(defn.id)

        runs = exec_repo.list_runs_by_session(session.id)
        assert len(runs) == 1
        assert runs[0].status == RunStatus.FAILED
        assert runs[0].failed_count == 1
        assert runs[0].ended_at is not None
        res_list = exec_repo.list_results_by_run(runs[0].id)
        assert res_list[0].status == TestResultStatus.ERROR
        assert "runner exploded" in res_list[0].error_message

        # Verify mark_interrupted_runs recomputes counts accurately
        from src.execution.models import TestCaseResult, TestRun

        interrupted = TestRun(
            project_id=project.id,
            session_id=session.id,
            mode=ExecutionMode.INDEPENDENT,
            status=RunStatus.RUNNING,
            total_count=2,
        )
        exec_repo.create_run(interrupted)
        exec_repo.create_results(
            [
                TestCaseResult(
                    run_id=interrupted.id,
                    function_name="test_one",
                    test_name="One",
                    status=TestResultStatus.PASSED,
                    order_index=1,
                ),
                TestCaseResult(
                    run_id=interrupted.id,
                    function_name="test_two",
                    test_name="Two",
                    status=TestResultStatus.PENDING,
                    order_index=2,
                ),
            ]
        )
        recovered = exec_service.recover_interrupted_runs()
        assert recovered == 1
        refreshed = exec_repo.get_run(interrupted.id)
        assert refreshed is not None
        assert refreshed.status == RunStatus.CANCELLED
        assert refreshed.passed_count == 1
        assert refreshed.skipped_count == 1
        assert refreshed.failed_count == 0
    finally:
        db.close_all()


@pytest.mark.browser
def test_e2e_playwright_actions_assertions_and_secret_env(
    tmp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    html_file = tmp_path / "rich_controls.html"
    html_file.write_text(
        """<!doctype html>
<html>
  <head><title>Rich Controls</title></head>
  <body>
    <form id="demo-form">
      <input id="secret" data-testid="secret-in" type="password" />
      <select id="plan" data-testid="plan-sel">
        <option value="free">Free</option>
        <option value="pro">Pro</option>
      </select>
      <input id="agree" data-testid="agree-chk" type="checkbox" />
      <input id="note" data-testid="note-in" type="text" value="initial" />
      <button id="hover-btn" data-testid="hover-btn" type="button"
              onmouseover="document.getElementById('tip').textContent='Hovered!';">
        Hover Me
      </button>
      <div id="tip" data-testid="tip-box"></div>
      <div id="out" data-testid="out-box"></div>
    </form>
    <script>
      document.getElementById("demo-form").addEventListener("submit", (e) => {
        e.preventDefault();
        const s = document.getElementById("secret").value;
        const p = document.getElementById("plan").value;
        document.getElementById("out").textContent = "Submitted:" + s + ":" + p;
      });
    </script>
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
        project = project_service.create_project(ProjectCreate(name="RichApp", base_url=page_url))
        session = session_service.create_session(project.id, name="RichSess", start_url=page_url)
        defn = TestDefinition(
            project_id=project.id,
            session_id=session.id,
            name="Rich Controls Flow",
            function_name="test_rich_controls_flow",
            mode=TestMode.INDEPENDENT,
            order_index=1,
            actions=[
                TestAction(
                    order_index=1,
                    action_type=ProcessedActionType.NAVIGATE,
                    summary="Open page",
                    url=page_url,
                ),
                TestAction(
                    order_index=2,
                    action_type=ProcessedActionType.FILL,
                    summary="Fill secret",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="secret-in"
                    ),
                    is_redacted=True,
                ),
                TestAction(
                    order_index=3,
                    action_type=ProcessedActionType.SELECT,
                    summary="Select Pro",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="plan-sel"),
                    value="pro",
                ),
                TestAction(
                    order_index=4,
                    action_type=ProcessedActionType.CHECK,
                    summary="Check agree",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="agree-chk"
                    ),
                    checked=True,
                ),
                TestAction(
                    order_index=5,
                    action_type=ProcessedActionType.HOVER,
                    summary="Hover button",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="hover-btn"
                    ),
                ),
                TestAction(
                    order_index=6,
                    action_type=ProcessedActionType.PRESS_KEY,
                    summary="Select all in note",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="note-in"),
                    key="Ctrl+a",
                ),
                TestAction(
                    order_index=7,
                    action_type=ProcessedActionType.SUBMIT,
                    summary="Submit form",
                    selector=SelectorCandidate(strategy=SelectorStrategy.CSS, value="#demo-form"),
                    via_form_submit=True,
                ),
            ],
            assertions=[
                TestAssertion(
                    order_index=1,
                    assertion_type=AssertionType.URL_EQUALS,
                    description="Exact URL",
                    expected=page_url,
                ),
                TestAssertion(
                    order_index=2,
                    assertion_type=AssertionType.ELEMENT_CHECKED,
                    description="Agree checked",
                    selector=SelectorCandidate(
                        strategy=SelectorStrategy.TEST_ID, value="agree-chk"
                    ),
                    expected="true",
                ),
                TestAssertion(
                    order_index=3,
                    assertion_type=AssertionType.ELEMENT_VALUE,
                    description="Note has value",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="note-in"),
                    expected="initial",
                ),
                TestAssertion(
                    order_index=4,
                    assertion_type=AssertionType.ELEMENT_VISIBLE,
                    description="Tip visible",
                    selector=SelectorCandidate(strategy=SelectorStrategy.TEST_ID, value="tip-box"),
                ),
                TestAssertion(
                    order_index=5,
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    description="Secret and plan submitted",
                    expected="Submitted:s3cr3t:pro",
                ),
            ],
            settings=TestExecutionSettings(timeout_ms=5000),
        )
        def_repo.replace_for_session(session.id, [defn])

        run, results = exec_service.run_complete_suite(
            session.id,
            extra_env={"TEST_SECRET_STEP_2": "s3cr3t"},
        )
        assert run.status == RunStatus.PASSED
        assert len(results) == 1
        assert results[0].status == TestResultStatus.PASSED
    finally:
        db.close_all()
