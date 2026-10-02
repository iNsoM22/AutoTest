"""Models for generated standalone Python/Playwright/Pytest test files."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedFile(BaseModel):
    """A single generated source or configuration file."""

    filename: str
    content: str
    description: str = ""


class GeneratedSuite(BaseModel):
    """A complete standalone Pytest + Playwright test suite."""

    conftest: GeneratedFile
    pytest_ini: GeneratedFile
    test_files: list[GeneratedFile] = Field(default_factory=list)
