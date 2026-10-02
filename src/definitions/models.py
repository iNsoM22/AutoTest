"""Models and exceptions for editable test definitions, actions, selectors, and assertions."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from src.processing.models import ProcessedActionType


class TestDefinitionError(Exception):
    """Domain error raised for invalid test definition operations."""

    __test__ = False


class TestMode(StrEnum):
    """Execution representation of a test definition."""

    INDEPENDENT = "independent"
    JOURNEY = "journey"


TestMode.__test__ = False  # type: ignore[attr-defined]


class SelectorStrategy(StrEnum):
    """Playwright-compatible locator strategies ranked by stability."""

    TEST_ID = "test_id"
    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    CSS = "css"


class AssertionType(StrEnum):
    """Supported deterministic assertion types derived from recorded outcomes."""

    URL_CONTAINS = "url_contains"
    URL_EQUALS = "url_equals"
    TEXT_VISIBLE = "text_visible"
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_CHECKED = "element_checked"
    ELEMENT_VALUE = "element_value"
    NETWORK_RESPONSE = "network_response"


class AssertionConfidence(StrEnum):
    """Evidence-strength classification for generated assertions."""

    DIRECTLY_OBSERVED = "directly_observed"
    CORRELATED = "correlated"
    INFERRED = "inferred"


class SelectorCandidate(BaseModel):
    """A structured target element selector with strategy and confidence score."""

    strategy: SelectorStrategy
    value: str
    role: str = ""
    name: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    def display(self) -> str:
        """Return a concise human-readable representation for UI tables."""
        if self.strategy == SelectorStrategy.ROLE:
            if self.name:
                return f"role={self.role}[name={self.name!r}]"
            return f"role={self.role}"
        return f"{self.strategy.value}={self.value}"


class TestPrerequisite(BaseModel):
    """An explicit setup requirement for an independent or journey test."""

    __test__: ClassVar[bool] = False

    name: str
    description: str
    fixture_name: str = ""
    setup_url: str = ""


class TestAction(BaseModel):
    """An editable user or browser action step within a test definition."""

    __test__: ClassVar[bool] = False

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    order_index: int = Field(ge=1)
    action_type: ProcessedActionType
    summary: str
    selector: SelectorCandidate | None = None
    fallback_selectors: list[SelectorCandidate] = Field(default_factory=list)
    value: str = ""
    url: str = ""
    key: str = ""
    checked: bool | None = None
    is_redacted: bool = False
    source_processed_event_id: str | None = None
    raw_event_ids: list[str] = Field(default_factory=list)


class TestAssertion(BaseModel):
    """An editable, evidence-backed assertion linked to recorded observations."""

    __test__: ClassVar[bool] = False

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    order_index: int = Field(ge=1)
    assertion_type: AssertionType
    confidence: AssertionConfidence = AssertionConfidence.DIRECTLY_OBSERVED
    needs_review: bool = False
    enabled: bool = True
    description: str
    selector: SelectorCandidate | None = None
    expected: str = ""
    after_action_id: str | None = None
    evidence_summary: str = ""
    raw_event_ids: list[str] = Field(default_factory=list)


class TestExecutionSettings(BaseModel):
    """Per-test execution configuration used by the generator and runner."""

    __test__: ClassVar[bool] = False

    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    headless: bool = True
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=720, ge=240, le=2160)
    base_url: str = ""


_FUNC_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def slugify_test_function_name(name: str) -> str:
    """Convert a human-readable test name into a valid Pytest `test_*` identifier."""
    slug = _FUNC_SANITIZE_RE.sub("_", name.strip().lower()).strip("_")
    if not slug:
        slug = "recorded_flow"
    if not slug.startswith("test_"):
        slug = f"test_{slug}"
    return slug


class TestDefinition(BaseModel):
    """Layer 3 editable test specification derived from reviewed scenarios."""

    __test__: ClassVar[bool] = False

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str
    scenario_id: str | None = None
    name: str
    function_name: str = ""
    description: str = ""
    mode: TestMode = TestMode.INDEPENDENT
    order_index: int = Field(ge=1)
    included: bool = True
    prerequisites: list[TestPrerequisite] = Field(default_factory=list)
    actions: list[TestAction] = Field(default_factory=list)
    assertions: list[TestAssertion] = Field(default_factory=list)
    settings: TestExecutionSettings = Field(default_factory=TestExecutionSettings)
    raw_event_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Test definition name cannot be empty.")
        return cleaned

    @field_validator("function_name", mode="after")
    @classmethod
    def _ensure_function_name(cls, value: str) -> str:
        cleaned = value.strip()
        return slugify_test_function_name(cleaned) if cleaned else ""
