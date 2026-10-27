"""Application service for executing test definitions, persisting history, and rerunning tests."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from src.definitions.models import TestDefinition, TestMode, slugify_test_function_name
from src.definitions.repository import TestDefinitionRepository
from src.execution.models import (
    ExecutionError,
    ExecutionMode,
    RunStatus,
    TestCaseResult,
    TestResultStatus,
    TestRun,
)
from src.execution.repository import ExecutionRepository
from src.execution.runner import ExecutionRunner, ProgressCallback
from src.export.service import CopyExportService
from src.projects.repository import ProjectRepository, SessionRepository
from src.storage.artifacts import ArtifactManager, ArtifactRepository


class ExecutionService:
    """Orchestrates independent and sequential journey test runs with SQLite history."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        session_repository: SessionRepository,
        definition_repository: TestDefinitionRepository,
        execution_repository: ExecutionRepository,
        artifacts: ArtifactManager,
        artifact_repository: ArtifactRepository,
        *,
        copy_service: CopyExportService | None = None,
        runner: ExecutionRunner | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._session_repo = session_repository
        self._definition_repo = definition_repository
        self._execution_repo = execution_repository
        self._artifacts = artifacts
        self._artifact_repo = artifact_repository
        self._runner = runner or ExecutionRunner(
            artifacts,
            artifact_repository,
            copy_service=copy_service,
        )

    def execute_definitions(
        self,
        session_id: str,
        definition_ids: Sequence[str],
        mode: ExecutionMode,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Execute the specified test definitions and persist run + per-test results."""
        session = self._session_repo.get(session_id)
        if session is None:
            raise ExecutionError(f"Recording session not found: {session_id}")
        project = self._project_repo.get(session.project_id)
        if project is None:
            raise ExecutionError(f"Project not found: {session.project_id}")

        all_defs = self._definition_repo.list_by_session(session_id)
        defs_by_id = {d.id: d for d in all_defs}
        selected: list[TestDefinition] = []
        for def_id in definition_ids:
            found = defs_by_id.get(def_id)
            if found is not None:
                selected.append(found)

        if not selected:
            raise ExecutionError("No test definitions selected to execute.")

        run = TestRun(
            project_id=project.id,
            session_id=session.id,
            mode=mode,
            status=RunStatus.PENDING,
            total_count=len(selected),
        )
        self._execution_repo.create_run(run)

        initial_results = [
            TestCaseResult(
                run_id=run.id,
                definition_id=defn.id,
                function_name=defn.function_name or slugify_test_function_name(defn.name),
                test_name=defn.name,
                status=TestResultStatus.PENDING,
                order_index=idx,
            )
            for idx, defn in enumerate(selected, start=1)
        ]
        self._execution_repo.create_results(initial_results)

        completed_run, completed_results = self._runner.execute(
            run,
            selected,
            initial_results,
            base_url=project.base_url,
            headed=headed,
            cancel_event=cancel_event,
            on_result_updated=self._execution_repo.update_result,
            on_event=on_event,
        )
        self._execution_repo.update_run(completed_run)
        return completed_run, completed_results

    def run_single_test(
        self,
        definition_id: str,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Run a single test definition in its natural mode."""
        defn = self._definition_repo.get(definition_id)
        if defn is None:
            raise ExecutionError(f"Test definition not found: {definition_id}")
        mode = ExecutionMode.JOURNEY if defn.mode == TestMode.JOURNEY else ExecutionMode.INDEPENDENT
        return self.execute_definitions(
            defn.session_id,
            [defn.id],
            mode,
            headed=headed,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def run_scenario(
        self,
        session_id: str,
        scenario_id: str,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Run all included independent test definitions belonging to a scenario."""
        defs = [
            d
            for d in self._definition_repo.list_by_session(session_id)
            if d.scenario_id == scenario_id and d.included
        ]
        if not defs:
            raise ExecutionError(f"No included tests found for scenario: {scenario_id}")
        return self.execute_definitions(
            session_id,
            [d.id for d in defs],
            ExecutionMode.INDEPENDENT,
            headed=headed,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def run_independent(
        self,
        session_id: str,
        definition_ids: Sequence[str] | None = None,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Run all (or selected) independent tests for a session."""
        if definition_ids is not None:
            ids = list(definition_ids)
        else:
            ids = [
                d.id
                for d in self._definition_repo.list_by_session(session_id)
                if d.mode == TestMode.INDEPENDENT and d.included
            ]
        if not ids:
            raise ExecutionError("No included independent tests available for this session.")
        return self.execute_definitions(
            session_id,
            ids,
            ExecutionMode.INDEPENDENT,
            headed=headed,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def run_journey(
        self,
        session_id: str,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Run the sequential journey test (or all included tests in order if no journey test)."""
        all_defs = [d for d in self._definition_repo.list_by_session(session_id) if d.included]
        journey_defs = [d for d in all_defs if d.mode == TestMode.JOURNEY]
        targets = journey_defs or all_defs
        if not targets:
            raise ExecutionError("No included journey tests available for this session.")
        return self.execute_definitions(
            session_id,
            [d.id for d in targets],
            ExecutionMode.JOURNEY,
            headed=headed,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def rerun_failed(
        self,
        run_id: str,
        *,
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Rerun only the failed/errored test definitions from a previous run."""
        previous = self._execution_repo.get_run(run_id)
        if previous is None:
            raise ExecutionError(f"Test run not found: {run_id}")
        prev_results = self._execution_repo.list_results_by_run(run_id)
        failed_ids = [
            r.definition_id
            for r in prev_results
            if r.status in (TestResultStatus.FAILED, TestResultStatus.ERROR)
            and r.definition_id is not None
        ]
        if not failed_ids:
            raise ExecutionError("Selected run has no failed tests to rerun.")
        return self.execute_definitions(
            previous.session_id,
            failed_ids,
            previous.mode,
            headed=headed,
            cancel_event=cancel_event,
            on_event=on_event,
        )

    def list_runs_by_session(self, session_id: str) -> list[TestRun]:
        """Return historical test runs for a recording session."""
        return self._execution_repo.list_runs_by_session(session_id)

    def list_runs_by_project(self, project_id: str) -> list[TestRun]:
        """Return historical test runs for a project."""
        return self._execution_repo.list_runs_by_project(project_id)

    def get_run_with_results(self, run_id: str) -> tuple[TestRun, list[TestCaseResult]]:
        """Fetch a persisted test run and its ordered per-test results."""
        run = self._execution_repo.get_run(run_id)
        if run is None:
            raise ExecutionError(f"Test run not found: {run_id}")
        results = self._execution_repo.list_results_by_run(run_id)
        return run, results

    def get_artifact_path(self, artifact_id: str | None) -> Path | None:
        """Resolve the filesystem path for a stored run artifact (log or screenshot)."""
        if not artifact_id:
            return None
        record = self._artifact_repo.get(artifact_id)
        if record is None:
            return None
        path = self._artifacts.resolve_path(record.relative_path)
        return path if path.is_file() else None

    def recover_interrupted_runs(self) -> int:
        """Mark any leftover pending/running test runs as cancelled on startup."""
        return self._execution_repo.mark_interrupted_runs(datetime.now(UTC))
