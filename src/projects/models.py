"""Pydantic domain models for projects, recording sessions, and tracked browser pages."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SessionStatus(StrEnum):
    """Lifecycle states of a recording session."""

    CREATED = "created"
    ACTIVE = "active"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class Project(BaseModel):
    """A workspace project grouping recordings, scenarios, and generated tests."""

    id: str
    name: str
    description: str = ""
    base_url: str = ""
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    """Input payload for creating a new project."""

    name: str
    description: str = ""
    base_url: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Project name must not be empty.")
        return cleaned

    @field_validator("description", "base_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ProjectUpdate(BaseModel):
    """Input payload for updating an existing project."""

    name: str | None = None
    description: str | None = None
    base_url: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Project name must not be empty.")
        return cleaned

    @field_validator("description", "base_url")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class RecordingSession(BaseModel):
    """A single browser recording session belonging to a project."""

    id: str
    project_id: str
    name: str
    start_url: str = ""
    status: SessionStatus = SessionStatus.CREATED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserPageRecord(BaseModel):
    """A browser page/tab tracked during a recording session."""

    id: str
    session_id: str
    tab_index: int
    initial_url: str = ""
    last_url: str = ""
    title: str = ""
    opened_at: datetime
    closed_at: datetime | None = None
