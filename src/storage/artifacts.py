"""Filesystem artifact directory management with safe relative path handling."""

from __future__ import annotations

from pathlib import Path


class ArtifactManager:
    """Manages local filesystem directories for project and session artifacts."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.expanduser().resolve()

    @property
    def base_dir(self) -> Path:
        """Return the resolved root artifacts directory."""
        return self._base_dir

    def ensure_base_dir(self) -> Path:
        """Create the root artifacts directory if it does not exist."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        return self._base_dir

    def project_dir(self, project_id: str) -> Path:
        """Return the artifact directory path for a project."""
        return self._safe_join("projects", project_id)

    def ensure_project_dir(self, project_id: str) -> Path:
        """Create and return the artifact directory for a project."""
        path = self.project_dir(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_dir(self, project_id: str, session_id: str) -> Path:
        """Return the artifact directory path for a recording session."""
        return self._safe_join("projects", project_id, "sessions", session_id)

    def ensure_session_dir(self, project_id: str, session_id: str) -> Path:
        """Create and return the artifact directory for a recording session."""
        path = self.session_dir(project_id, session_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a stored relative artifact path and guard against path traversal."""
        candidate = (self._base_dir / relative_path).resolve()
        if not candidate.is_relative_to(self._base_dir):
            raise ValueError(f"Artifact path escapes base directory: {relative_path!r}")
        return candidate

    def to_relative_path(self, path: Path) -> str:
        """Convert an absolute path inside `base_dir` to a portable POSIX relative string."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self._base_dir):
            raise ValueError(f"Path is outside artifacts base directory: {path}")
        return resolved.relative_to(self._base_dir).as_posix()

    def _safe_join(self, *parts: str) -> Path:
        for part in parts:
            if not part or "/" in part or "\\" in part or part in {".", ".."}:
                raise ValueError(f"Invalid artifact path segment: {part!r}")
        candidate = self._base_dir.joinpath(*parts).resolve()
        if not candidate.is_relative_to(self._base_dir):
            raise ValueError("Computed artifact path escapes base directory.")
        return candidate
