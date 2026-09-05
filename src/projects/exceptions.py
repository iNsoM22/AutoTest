"""Domain exceptions for project and recording session management."""

from __future__ import annotations


class ProjectError(Exception):
    """Base exception for project and session domain errors."""


class ProjectNotFoundError(ProjectError):
    """Raised when a requested project does not exist."""


class DuplicateProjectNameError(ProjectError):
    """Raised when attempting to create or rename a project to an existing name."""


class SessionNotFoundError(ProjectError):
    """Raised when a requested recording session does not exist."""


class SessionStateError(ProjectError):
    """Raised when a recording session state transition is invalid."""
