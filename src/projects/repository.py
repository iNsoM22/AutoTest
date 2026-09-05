"""SQLite repositories for projects, recording sessions, and browser pages."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from src.projects.exceptions import DuplicateProjectNameError
from src.projects.models import BrowserPageRecord, Project, RecordingSession, SessionStatus
from src.storage.database import Database


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=str(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        base_url=str(row["base_url"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _row_to_session(row: sqlite3.Row) -> RecordingSession:
    raw_meta = str(row["metadata_json"])
    metadata: dict[str, Any] = json.loads(raw_meta) if raw_meta else {}
    return RecordingSession(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        start_url=str(row["start_url"]),
        status=SessionStatus(str(row["status"])),
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        metadata=metadata,
    )


def _row_to_page(row: sqlite3.Row) -> BrowserPageRecord:
    return BrowserPageRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        tab_index=int(row["tab_index"]),
        initial_url=str(row["initial_url"]),
        last_url=str(row["last_url"]),
        title=str(row["title"]),
        opened_at=datetime.fromisoformat(str(row["opened_at"])),
        closed_at=_parse_dt(row["closed_at"]),
    )


class ProjectRepository:
    """Persistence operations for `projects`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, project: Project) -> Project:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO projects (id, name, description, base_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        project.id,
                        project.name,
                        project.description,
                        project.base_url,
                        _format_dt(project.created_at),
                        _format_dt(project.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateProjectNameError(
                f"A project named {project.name!r} already exists."
            ) from exc
        return project

    def get(self, project_id: str) -> Project | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
            .fetchone()
        )
        return _row_to_project(row) if row is not None else None

    def get_by_name(self, name: str) -> Project | None:
        row = (
            self._db.connect().execute("SELECT * FROM projects WHERE name = ?;", (name,)).fetchone()
        )
        return _row_to_project(row) if row is not None else None

    def list_all(self) -> list[Project]:
        rows = (
            self._db.connect()
            .execute("SELECT * FROM projects ORDER BY updated_at DESC, name ASC;")
            .fetchall()
        )
        return [_row_to_project(row) for row in rows]

    def update(self, project: Project) -> Project:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE projects
                    SET name = ?, description = ?, base_url = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (
                        project.name,
                        project.description,
                        project.base_url,
                        _format_dt(project.updated_at),
                        project.id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateProjectNameError(
                f"A project named {project.name!r} already exists."
            ) from exc
        return project

    def delete(self, project_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?;", (project_id,))
            return cursor.rowcount > 0


class SessionRepository:
    """Persistence operations for `recording_sessions` and `browser_pages`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, session: RecordingSession) -> RecordingSession:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO recording_sessions (
                    id, project_id, name, start_url, status,
                    started_at, ended_at, created_at, updated_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session.id,
                    session.project_id,
                    session.name,
                    session.start_url,
                    session.status.value,
                    _format_dt(session.started_at),
                    _format_dt(session.ended_at),
                    _format_dt(session.created_at),
                    _format_dt(session.updated_at),
                    json.dumps(session.metadata),
                ),
            )
        return session

    def get(self, session_id: str) -> RecordingSession | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM recording_sessions WHERE id = ?;", (session_id,))
            .fetchone()
        )
        return _row_to_session(row) if row is not None else None

    def list_by_project(self, project_id: str) -> list[RecordingSession]:
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM recording_sessions
                WHERE project_id = ?
                ORDER BY created_at DESC;
                """,
                (project_id,),
            )
            .fetchall()
        )
        return [_row_to_session(row) for row in rows]

    def list_active(self, project_id: str | None = None) -> list[RecordingSession]:
        if project_id is not None:
            rows = (
                self._db.connect()
                .execute(
                    """
                    SELECT * FROM recording_sessions
                    WHERE project_id = ? AND status = ?
                    ORDER BY created_at DESC;
                    """,
                    (project_id, SessionStatus.ACTIVE.value),
                )
                .fetchall()
            )
        else:
            rows = (
                self._db.connect()
                .execute(
                    """
                    SELECT * FROM recording_sessions
                    WHERE status = ?
                    ORDER BY created_at DESC;
                    """,
                    (SessionStatus.ACTIVE.value,),
                )
                .fetchall()
            )
        return [_row_to_session(row) for row in rows]

    def update(self, session: RecordingSession) -> RecordingSession:
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE recording_sessions
                SET name = ?, start_url = ?, status = ?, started_at = ?, ended_at = ?,
                    updated_at = ?, metadata_json = ?
                WHERE id = ?;
                """,
                (
                    session.name,
                    session.start_url,
                    session.status.value,
                    _format_dt(session.started_at),
                    _format_dt(session.ended_at),
                    _format_dt(session.updated_at),
                    json.dumps(session.metadata),
                    session.id,
                ),
            )
        return session

    def delete(self, session_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM recording_sessions WHERE id = ?;", (session_id,))
            return cursor.rowcount > 0

    def mark_interrupted_sessions(self, ended_at: datetime) -> int:
        ts = _format_dt(ended_at)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE recording_sessions
                SET status = ?, ended_at = COALESCE(ended_at, ?), updated_at = ?
                WHERE status = ?;
                """,
                (SessionStatus.INTERRUPTED.value, ts, ts, SessionStatus.ACTIVE.value),
            )
            return cursor.rowcount

    def add_page(self, page: BrowserPageRecord) -> BrowserPageRecord:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO browser_pages (
                    id, session_id, tab_index, initial_url, last_url, title, opened_at, closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    page.id,
                    page.session_id,
                    page.tab_index,
                    page.initial_url,
                    page.last_url,
                    page.title,
                    _format_dt(page.opened_at),
                    _format_dt(page.closed_at),
                ),
            )
        return page

    def update_page(
        self,
        page_id: str,
        *,
        last_url: str | None = None,
        title: str | None = None,
        closed_at: datetime | None = None,
    ) -> BrowserPageRecord | None:
        existing = self.get_page(page_id)
        if existing is None:
            return None
        updated = existing.model_copy(
            update={
                "last_url": last_url if last_url is not None else existing.last_url,
                "title": title if title is not None else existing.title,
                "closed_at": closed_at if closed_at is not None else existing.closed_at,
            }
        )
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE browser_pages
                SET last_url = ?, title = ?, closed_at = ?
                WHERE id = ?;
                """,
                (
                    updated.last_url,
                    updated.title,
                    _format_dt(updated.closed_at),
                    updated.id,
                ),
            )
        return updated

    def get_page(self, page_id: str) -> BrowserPageRecord | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM browser_pages WHERE id = ?;", (page_id,))
            .fetchone()
        )
        return _row_to_page(row) if row is not None else None

    def list_pages(self, session_id: str) -> list[BrowserPageRecord]:
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM browser_pages
                WHERE session_id = ?
                ORDER BY tab_index ASC, opened_at ASC;
                """,
                (session_id,),
            )
            .fetchall()
        )
        return [_row_to_page(row) for row in rows]
