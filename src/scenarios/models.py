"""Models and exceptions for detected and user-edited scenarios."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class ScenarioError(Exception):
    """Domain error raised for invalid scenario operations."""


class Scenario(BaseModel):
    """A logical recorded workflow derived from processed events."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str
    name: str
    description: str = ""
    order_index: int = Field(ge=1)
    included: bool = True
    processed_event_ids: list[str] = Field(default_factory=list)
    raw_event_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Scenario name cannot be empty.")
        return cleaned
