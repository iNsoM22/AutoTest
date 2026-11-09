"""Filesystem artifact directory management and SQLite artifact metadata persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.storage.database import Database


class ArtifactRecord(BaseModel):
    """Metadata row stored in SQLite `artifacts` pointing to a local file."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    session_id: str | None = None
    artifact_type: str
    relative_path: str
    mime_type: str = ""
    size_bytes: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


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

    def run_dir(self, project_id: str, session_id: str, run_id: str) -> Path:
        """Return the artifact directory path for a test execution run."""
        return self._safe_join("projects", project_id, "sessions", session_id, "runs", run_id)

    def ensure_run_dir(self, project_id: str, session_id: str, run_id: str) -> Path:
        """Create and return the artifact directory for a test execution run."""
        path = self.run_dir(project_id, session_id, run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_bytes(self, directory: Path, filename: str, content: bytes) -> tuple[Path, str]:
        if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError(f"Invalid artifact filename: {filename!r}")
        target = (directory / filename).resolve()
        if not target.is_relative_to(self._base_dir):
            raise ValueError("Computed artifact file path escapes base directory.")
        target.write_bytes(content)
        return target, self.to_relative_path(target)

    def save_run_bytes(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        filename: str,
        content: bytes,
    ) -> tuple[Path, str]:
        """Write bytes into a test run artifact directory and return `(path, relative_path)`."""
        return self._write_bytes(
            self.ensure_run_dir(project_id, session_id, run_id), filename, content
        )

    def save_session_bytes(
        self,
        project_id: str,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> tuple[Path, str]:
        """Write bytes into a session artifact directory and return `(path, relative_path)`."""
        return self._write_bytes(self.ensure_session_dir(project_id, session_id), filename, content)

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


class ArtifactRepository:
    """SQLite persistence for artifact metadata rows."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, record: ArtifactRecord) -> ArtifactRecord:
        """Insert a new artifact metadata row."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    id, project_id, session_id, artifact_type, relative_path,
                    mime_type, size_bytes, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.project_id,
                    record.session_id,
                    record.artifact_type,
                    record.relative_path,
                    record.mime_type,
                    record.size_bytes,
                    record.created_at.isoformat(),
                    json.dumps(record.metadata, ensure_ascii=False),
                ),
            )
        return record

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        """Fetch a single artifact metadata record by ID."""
        row = (
            self._db.connect()
            .execute("SELECT * FROM artifacts WHERE id = ?;", (artifact_id,))
            .fetchone()
        )
        return self._row_to_record(row) if row is not None else None

    def list_by_session(self, session_id: str) -> list[ArtifactRecord]:
        """Return all artifact records associated with a recording session."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM artifacts
                WHERE session_id = ?
                ORDER BY created_at ASC;
                """,
                (session_id,),
            )
            .fetchall()
        )
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            artifact_type=row["artifact_type"],
            relative_path=row["relative_path"],
            mime_type=row["mime_type"],
            size_bytes=int(row["size_bytes"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
