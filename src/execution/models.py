"""Models and exceptions for built-in test execution runs and results."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class ExecutionError(Exception):
    """Domain error raised for invalid or failed test execution operations."""


class ExecutionMode(StrEnum):
    """Execution strategy for running generated tests."""

    INDEPENDENT = "independent"
    JOURNEY = "journey"


class RunStatus(StrEnum):
    """Overall lifecycle status of a test execution run."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TestResultStatus(StrEnum):
    """Per-test execution status."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


TestResultStatus.__test__ = False  # type: ignore[attr-defined]


class TestCaseResult(BaseModel):
    """Persisted outcome for a single executed test function within a `TestRun`."""

    __test__: ClassVar[bool] = False

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    definition_id: str | None = None
    function_name: str
    test_name: str
    status: TestResultStatus = TestResultStatus.PENDING
    order_index: int = Field(default=1, ge=1)
    duration_ms: int = Field(default=0, ge=0)
    error_message: str = ""
    traceback: str = ""
    stdout: str = ""
    stderr: str = ""
    screenshot_artifact_id: str | None = None
    console_errors: list[str] = Field(default_factory=list)
    network_errors: list[str] = Field(default_factory=list)


class TestRun(BaseModel):
    """Persisted summary of a single built-in Pytest execution run."""

    __test__: ClassVar[bool] = False

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str
    mode: ExecutionMode
    status: RunStatus = RunStatus.PENDING
    total_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    log_artifact_id: str | None = None


class ExecutionProgressEvent(BaseModel):
    """Live progress event emitted from the execution runner to observers/UI."""

    event_type: str  # run_started | test_started | test_finished | log | run_finished
    run_id: str
    function_name: str = ""
    status: str = ""
    duration_ms: int = 0
    message: str = ""
    result: TestCaseResult | None = None
    run: TestRun | None = None
