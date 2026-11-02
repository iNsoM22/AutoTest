"""Application services for project CRUD and recording session lifecycle."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime

from src.projects.exceptions import (
    ProjectNotFoundError,
    SessionNotFoundError,
    SessionStateError,
)
from src.projects.models import (
    BrowserPageRecord,
    Project,
    ProjectCreate,
    ProjectUpdate,
    RecordingSession,
    SessionStatus,
)
from src.projects.repository import ProjectRepository, SessionRepository
from src.storage.artifacts import ArtifactManager

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProjectService:
    """High-level operations for managing AutoTest projects."""

    def __init__(
        self,
        repository: ProjectRepository,
        artifact_manager: ArtifactManager,
    ) -> None:
        self._repo = repository
        self._artifacts = artifact_manager

    def create_project(self, payload: ProjectCreate) -> Project:
        now = _utc_now()
        project = Project(
            id=_new_id(),
            name=payload.name,
            description=payload.description,
            base_url=payload.base_url,
            created_at=now,
            updated_at=now,
        )
        created = self._repo.create(project)
        self._artifacts.ensure_project_dir(created.id)
        logger.info("Created project %s (%s)", created.name, created.id)
        return created

    def get_project(self, project_id: str) -> Project:
        project = self._repo.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        return project

    def list_projects(self) -> list[Project]:
        return self._repo.list_all()

    def update_project(self, project_id: str, payload: ProjectUpdate) -> Project:
        existing = self.get_project(project_id)
        updated = existing.model_copy(
            update={
                "name": payload.name if payload.name is not None else existing.name,
                "description": (
                    payload.description if payload.description is not None else existing.description
                ),
                "base_url": (
                    payload.base_url if payload.base_url is not None else existing.base_url
                ),
                "updated_at": _utc_now(),
            }
        )
        saved = self._repo.update(updated)
        logger.info("Updated project %s (%s)", saved.name, saved.id)
        return saved

    def delete_project(self, project_id: str) -> None:
        deleted = self._repo.delete(project_id)
        if not deleted:
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        shutil.rmtree(self._artifacts.project_dir(project_id), ignore_errors=True)
        logger.info("Deleted project %s", project_id)


class SessionService:
    """Manages recording session lifecycle and tracked browser pages."""

    def __init__(
        self,
        session_repository: SessionRepository,
        project_repository: ProjectRepository,
        artifact_manager: ArtifactManager,
    ) -> None:
        self._sessions = session_repository
        self._projects = project_repository
        self._artifacts = artifact_manager

    def create_session(
        self,
        project_id: str,
        *,
        name: str | None = None,
        start_url: str | None = None,
    ) -> RecordingSession:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project not found: {project_id}")

        now = _utc_now()
        resolved_name = (name or "").strip() or f"Session {now.strftime('%Y-%m-%d %H:%M:%S')}"
        resolved_url = (start_url if start_url is not None else project.base_url).strip()

        session = RecordingSession(
            id=_new_id(),
            project_id=project.id,
            name=resolved_name,
            start_url=resolved_url,
            status=SessionStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        created = self._sessions.create(session)
        self._artifacts.ensure_session_dir(project.id, created.id)
        logger.info("Created recording session %s for project %s", created.id, project.id)
        return created

    def get_session(self, session_id: str) -> RecordingSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Recording session not found: {session_id}")
        return session

    def list_sessions(self, project_id: str) -> list[RecordingSession]:
        return self._sessions.list_by_project(project_id)

    def start_session(self, session_id: str) -> RecordingSession:
        session = self.get_session(session_id)
        if session.status != SessionStatus.CREATED:
            raise SessionStateError(
                f"Cannot start session in state {session.status.value!r}; expected 'created'."
            )

        active = self._sessions.list_active(session.project_id)
        if active:
            raise SessionStateError(
                f"Project {session.project_id} already has an active session ({active[0].id})."
            )

        now = _utc_now()
        updated = session.model_copy(
            update={
                "status": SessionStatus.ACTIVE,
                "started_at": now,
                "updated_at": now,
            }
        )
        saved = self._sessions.update(updated)
        logger.info("Started recording session %s", saved.id)
        return saved

    def stop_session(self, session_id: str) -> RecordingSession:
        session = self.get_session(session_id)
        if session.status != SessionStatus.ACTIVE:
            raise SessionStateError(
                f"Cannot stop session in state {session.status.value!r}; expected 'active'."
            )

        now = _utc_now()
        updated = session.model_copy(
            update={
                "status": SessionStatus.STOPPED,
                "ended_at": now,
                "updated_at": now,
            }
        )
        saved = self._sessions.update(updated)
        logger.info("Stopped recording session %s", saved.id)
        return saved

    def fail_session(self, session_id: str, error_message: str) -> RecordingSession:
        session = self.get_session(session_id)
        if session.status not in {SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise SessionStateError(
                f"Cannot fail session in terminal state {session.status.value!r}."
            )

        now = _utc_now()
        metadata = dict(session.metadata)
        metadata["error"] = error_message
        updated = session.model_copy(
            update={
                "status": SessionStatus.FAILED,
                "ended_at": now,
                "updated_at": now,
                "metadata": metadata,
            }
        )
        saved = self._sessions.update(updated)
        logger.warning("Marked recording session %s as failed: %s", saved.id, error_message)
        return saved

    def recover_interrupted_sessions(self) -> int:
        """Mark any leftover active sessions as interrupted on application startup."""
        count = self._sessions.mark_interrupted_sessions(_utc_now())
        if count:
            logger.warning("Recovered %d interrupted recording session(s).", count)
        return count

    def delete_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Recording session not found: {session_id}")
        self._sessions.delete(session_id)
        shutil.rmtree(
            self._artifacts.session_dir(session.project_id, session_id),
            ignore_errors=True,
        )
        logger.info("Deleted recording session %s", session_id)

    def register_page(
        self,
        session_id: str,
        *,
        page_id: str,
        tab_index: int,
        url: str = "",
        title: str = "",
    ) -> BrowserPageRecord:
        self.get_session(session_id)
        record = BrowserPageRecord(
            id=page_id,
            session_id=session_id,
            tab_index=tab_index,
            initial_url=url,
            last_url=url,
            title=title,
            opened_at=_utc_now(),
        )
        return self._sessions.add_page(record)

    def update_page(
        self,
        page_id: str,
        *,
        url: str | None = None,
        title: str | None = None,
    ) -> BrowserPageRecord | None:
        return self._sessions.update_page(page_id, last_url=url, title=title)

    def close_page(self, page_id: str) -> BrowserPageRecord | None:
        return self._sessions.update_page(page_id, closed_at=_utc_now())

    def list_pages(self, session_id: str) -> list[BrowserPageRecord]:
        return self._sessions.list_pages(session_id)
