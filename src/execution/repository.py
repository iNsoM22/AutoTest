"""SQLite repository for test execution runs and per-test results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from src.execution.models import ExecutionMode, RunStatus, TestCaseResult, TestResultStatus, TestRun
from src.storage.database import Database


class ExecutionRepository:
    """Persists and queries `test_runs` and `test_results` rows in SQLite."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_run(self, run: TestRun) -> TestRun:
        """Insert a new test run record."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO test_runs (
                    id, project_id, session_id, mode, status,
                    total_count, passed_count, failed_count, skipped_count,
                    started_at, ended_at, duration_ms, summary_json, log_artifact_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run.id,
                    run.project_id,
                    run.session_id,
                    run.mode.value,
                    run.status.value,
                    run.total_count,
                    run.passed_count,
                    run.failed_count,
                    run.skipped_count,
                    run.started_at.isoformat(),
                    run.ended_at.isoformat() if run.ended_at else None,
                    run.duration_ms,
                    json.dumps(run.summary, ensure_ascii=False),
                    run.log_artifact_id,
                ),
            )
        return run

    def update_run(self, run: TestRun) -> TestRun:
        """Update an existing test run record."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE test_runs
                SET status = ?,
                    total_count = ?,
                    passed_count = ?,
                    failed_count = ?,
                    skipped_count = ?,
                    ended_at = ?,
                    duration_ms = ?,
                    summary_json = ?,
                    log_artifact_id = ?
                WHERE id = ?;
                """,
                (
                    run.status.value,
                    run.total_count,
                    run.passed_count,
                    run.failed_count,
                    run.skipped_count,
                    run.ended_at.isoformat() if run.ended_at else None,
                    run.duration_ms,
                    json.dumps(run.summary, ensure_ascii=False),
                    run.log_artifact_id,
                    run.id,
                ),
            )
        return run

    def get_run(self, run_id: str) -> TestRun | None:
        """Fetch a single test run by ID."""
        row = (
            self._db.connect()
            .execute("SELECT * FROM test_runs WHERE id = ?;", (run_id,))
            .fetchone()
        )
        return self._row_to_run(row) if row is not None else None

    def list_runs_by_session(self, session_id: str, limit: int = 50) -> list[TestRun]:
        """Return test runs for a session ordered newest first."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM test_runs
                WHERE session_id = ?
                ORDER BY started_at DESC
                LIMIT ?;
                """,
                (session_id, limit),
            )
            .fetchall()
        )
        return [self._row_to_run(row) for row in rows]

    def list_runs_by_project(self, project_id: str, limit: int = 50) -> list[TestRun]:
        """Return test runs for a project ordered newest first."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM test_runs
                WHERE project_id = ?
                ORDER BY started_at DESC
                LIMIT ?;
                """,
                (project_id, limit),
            )
            .fetchall()
        )
        return [self._row_to_run(row) for row in rows]

    def delete_run(self, run_id: str) -> bool:
        """Delete a test run and its cascaded results."""
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM test_runs WHERE id = ?;", (run_id,))
        return cursor.rowcount > 0

    def mark_interrupted_runs(self, ended_at: datetime) -> int:
        """Mark leftover pending/running test runs as cancelled on startup recovery."""
        ts = ended_at.isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE test_results
                SET status = ?,
                    error_message = CASE
                        WHEN error_message = '' THEN 'Interrupted by unexpected shutdown.'
                        ELSE error_message
                    END
                WHERE status IN (?, ?)
                  AND run_id IN (
                      SELECT id FROM test_runs WHERE status IN (?, ?)
                  );
                """,
                (
                    TestResultStatus.SKIPPED.value,
                    TestResultStatus.PENDING.value,
                    TestResultStatus.RUNNING.value,
                    RunStatus.PENDING.value,
                    RunStatus.RUNNING.value,
                ),
            )
            cursor = conn.execute(
                """
                UPDATE test_runs
                SET status = ?,
                    ended_at = COALESCE(ended_at, ?),
                    passed_count = (
                        SELECT COUNT(*) FROM test_results
                        WHERE run_id = test_runs.id AND status = ?
                    ),
                    failed_count = (
                        SELECT COUNT(*) FROM test_results
                        WHERE run_id = test_runs.id AND status IN (?, ?)
                    ),
                    skipped_count = (
                        SELECT COUNT(*) FROM test_results
                        WHERE run_id = test_runs.id AND status = ?
                    ),
                    total_count = (
                        SELECT COUNT(*) FROM test_results
                        WHERE run_id = test_runs.id
                    )
                WHERE status IN (?, ?);
                """,
                (
                    RunStatus.CANCELLED.value,
                    ts,
                    TestResultStatus.PASSED.value,
                    TestResultStatus.FAILED.value,
                    TestResultStatus.ERROR.value,
                    TestResultStatus.SKIPPED.value,
                    RunStatus.PENDING.value,
                    RunStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount

    def create_results(self, results: Sequence[TestCaseResult]) -> list[TestCaseResult]:
        """Insert initial per-test result rows for a run in a single transaction."""
        rows = [
            (
                r.id,
                r.run_id,
                r.definition_id,
                r.function_name,
                r.test_name,
                r.status.value,
                r.order_index,
                r.duration_ms,
                r.error_message,
                r.traceback,
                r.stdout,
                r.stderr,
                r.screenshot_artifact_id,
                json.dumps(r.console_errors, ensure_ascii=False),
                json.dumps(r.network_errors, ensure_ascii=False),
            )
            for r in results
        ]
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO test_results (
                    id, run_id, definition_id, function_name, test_name,
                    status, order_index, duration_ms, error_message, traceback,
                    stdout, stderr, screenshot_artifact_id,
                    console_errors_json, network_errors_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
        return list(results)

    def update_result(self, result: TestCaseResult) -> TestCaseResult:
        """Update a single per-test result row."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE test_results
                SET status = ?,
                    duration_ms = ?,
                    error_message = ?,
                    traceback = ?,
                    stdout = ?,
                    stderr = ?,
                    screenshot_artifact_id = ?,
                    console_errors_json = ?,
                    network_errors_json = ?
                WHERE id = ?;
                """,
                (
                    result.status.value,
                    result.duration_ms,
                    result.error_message,
                    result.traceback,
                    result.stdout,
                    result.stderr,
                    result.screenshot_artifact_id,
                    json.dumps(result.console_errors, ensure_ascii=False),
                    json.dumps(result.network_errors, ensure_ascii=False),
                    result.id,
                ),
            )
        return result

    def list_results_by_run(self, run_id: str) -> list[TestCaseResult]:
        """Return all per-test results for a run in execution order."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM test_results
                WHERE run_id = ?
                ORDER BY order_index ASC;
                """,
                (run_id,),
            )
            .fetchall()
        )
        return [self._row_to_result(row) for row in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> TestRun:
        return TestRun(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            mode=ExecutionMode(row["mode"]),
            status=RunStatus(row["status"]),
            total_count=int(row["total_count"]),
            passed_count=int(row["passed_count"]),
            failed_count=int(row["failed_count"]),
            skipped_count=int(row["skipped_count"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            duration_ms=int(row["duration_ms"]),
            summary=json.loads(row["summary_json"] or "{}"),
            log_artifact_id=row["log_artifact_id"],
        )

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> TestCaseResult:
        return TestCaseResult(
            id=row["id"],
            run_id=row["run_id"],
            definition_id=row["definition_id"],
            function_name=row["function_name"],
            test_name=row["test_name"],
            status=TestResultStatus(row["status"]),
            order_index=int(row["order_index"]),
            duration_ms=int(row["duration_ms"]),
            error_message=row["error_message"],
            traceback=row["traceback"],
            stdout=row["stdout"],
            stderr=row["stderr"],
            screenshot_artifact_id=row["screenshot_artifact_id"],
            console_errors=json.loads(row["console_errors_json"] or "[]"),
            network_errors=json.loads(row["network_errors_json"] or "[]"),
        )
