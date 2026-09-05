"""Tests for project CRUD, session lifecycle, recovery, and browser page tracking."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.config import AppSettings
from src.projects import (
    DuplicateProjectNameError,
    ProjectCreate,
    ProjectNotFoundError,
    ProjectRepository,
    ProjectService,
    ProjectUpdate,
    SessionRepository,
    SessionService,
    SessionStateError,
    SessionStatus,
)
from src.storage import ArtifactManager, Database


@pytest.fixture
def services(
    tmp_settings: AppSettings,
) -> Iterator[tuple[ProjectService, SessionService, ArtifactManager]]:
    db = Database(tmp_settings.db_path)
    db.initialize()
    artifacts = ArtifactManager(tmp_settings.artifacts_dir)
    project_repo = ProjectRepository(db)
    session_repo = SessionRepository(db)
    project_service = ProjectService(project_repo, artifacts)
    session_service = SessionService(session_repo, project_repo, artifacts)
    try:
        yield project_service, session_service, artifacts
    finally:
        db.close_all()


def test_project_crud_and_duplicate_name_guard(
    services: tuple[ProjectService, SessionService, ArtifactManager],
) -> None:
    project_service, _, artifacts = services

    created = project_service.create_project(
        ProjectCreate(
            name="  Shop App  ",
            description="E-commerce checkout tests",
            base_url="https://shop.example.com",
        )
    )
    assert created.name == "Shop App"
    assert artifacts.project_dir(created.id).is_dir()

    with pytest.raises(DuplicateProjectNameError):
        project_service.create_project(ProjectCreate(name="Shop App"))

    updated = project_service.update_project(
        created.id,
        ProjectUpdate(name="Shop Web", base_url="https://web.example.com"),
    )
    assert updated.name == "Shop Web"
    assert updated.base_url == "https://web.example.com"
    assert [p.id for p in project_service.list_projects()] == [created.id]

    project_service.delete_project(created.id)
    with pytest.raises(ProjectNotFoundError):
        project_service.get_project(created.id)


def test_session_lifecycle_and_active_guard(
    services: tuple[ProjectService, SessionService, ArtifactManager],
) -> None:
    project_service, session_service, artifacts = services
    project = project_service.create_project(
        ProjectCreate(name="Portal", base_url="https://portal.example.com")
    )

    session = session_service.create_session(project.id)
    assert session.status == SessionStatus.CREATED
    assert session.start_url == "https://portal.example.com"
    assert artifacts.session_dir(project.id, session.id).is_dir()

    started = session_service.start_session(session.id)
    assert started.status == SessionStatus.ACTIVE
    assert started.started_at is not None

    # Second concurrent active session in same project must be rejected
    second = session_service.create_session(project.id, name="Second")
    with pytest.raises(SessionStateError, match="already has an active session"):
        session_service.start_session(second.id)

    stopped = session_service.stop_session(session.id)
    assert stopped.status == SessionStatus.STOPPED
    assert stopped.ended_at is not None

    # Stopping an already stopped session fails
    with pytest.raises(SessionStateError):
        session_service.stop_session(session.id)

    # Failing a created session records error metadata
    failed = session_service.fail_session(second.id, "Chromium failed to launch")
    assert failed.status == SessionStatus.FAILED
    assert failed.metadata["error"] == "Chromium failed to launch"


def test_recover_interrupted_sessions_and_page_tracking(
    services: tuple[ProjectService, SessionService, ArtifactManager],
) -> None:
    project_service, session_service, _ = services
    project = project_service.create_project(ProjectCreate(name="CRM"))

    session = session_service.create_session(project.id, start_url="https://crm.example.com")
    session_service.start_session(session.id)

    page = session_service.register_page(
        session.id,
        page_id="page-1",
        tab_index=0,
        url="https://crm.example.com/login",
        title="Sign In",
    )
    assert page.tab_index == 0

    updated_page = session_service.update_page(
        "page-1",
        url="https://crm.example.com/dashboard",
        title="Dashboard",
    )
    assert updated_page is not None
    assert updated_page.last_url == "https://crm.example.com/dashboard"

    closed_page = session_service.close_page("page-1")
    assert closed_page is not None
    assert closed_page.closed_at is not None

    pages = session_service.list_pages(session.id)
    assert len(pages) == 1
    assert pages[0].title == "Dashboard"

    recovered = session_service.recover_interrupted_sessions()
    assert recovered == 1
    assert session_service.get_session(session.id).status == SessionStatus.INTERRUPTED
