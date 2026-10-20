"""GUI tests for the PySide6 desktop shell using pytest-qt."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from src.app import AppContext, create_app_context
from src.browser import BrowserCallbacks, BrowserLaunchConfig, BrowserManager, PageInfo
from src.config import AppSettings
from src.projects import ProjectCreate, SessionStatus
from src.ui import MainWindow


class _FakeBrowserManager(BrowserManager):
    """Lightweight in-memory stand-in for Playwright Chromium during UI tests."""

    def __init__(self, callbacks: BrowserCallbacks) -> None:
        super().__init__(callbacks=callbacks)
        self._running = False
        self._page = PageInfo(
            page_id="fake-page-1",
            tab_index=0,
            url="https://demo.example.com",
            title="Demo App",
            is_closed=False,
        )

    def launch(self, config: BrowserLaunchConfig) -> PageInfo:
        self._running = True
        self._page = self._page.model_copy(update={"url": config.start_url})
        if self._callbacks.on_page_opened is not None:
            self._callbacks.on_page_opened(self._page)
        return self._page

    def poll_events(self, timeout_ms: int = 100) -> bool:
        time.sleep(min(timeout_ms, 20) / 1000.0)
        return self._running

    def close(self) -> None:
        if self._running:
            self._running = False
            closed = self._page.model_copy(update={"is_closed": True})
            if self._callbacks.on_page_closed is not None:
                self._callbacks.on_page_closed(closed)


@pytest.fixture
def app_context(tmp_settings: AppSettings) -> Iterator[AppContext]:
    ctx = create_app_context(tmp_settings)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.mark.ui
def test_main_window_displays_projects_and_runs_session_lifecycle(
    qtbot: QtBot,
    app_context: AppContext,
) -> None:
    project = app_context.project_service.create_project(
        ProjectCreate(
            name="Billing Portal",
            base_url="https://billing.example.com",
            description="Invoices and payments",
        )
    )

    window = MainWindow(
        app_context.settings,
        app_context.project_service,
        app_context.session_service,
        app_context.event_repository,
        scenario_service=app_context.scenario_service,
        definition_service=app_context.definition_service,
        copy_service=app_context.copy_service,
        execution_service=app_context.execution_service,
        browser_manager_factory=_FakeBrowserManager,
    )
    qtbot.addWidget(window)
    window.show()

    assert window.project_list.count() == 1
    assert window.project_title_label.text() == "Billing Portal"
    assert window.start_url_input.text() == "https://billing.example.com"

    # Start recording session via button click
    window.start_recording_session()
    qtbot.waitUntil(lambda: window.status_badge.text() == "Recording Active", timeout=5000)
    qtbot.waitUntil(lambda: window.pages_table.rowCount() == 1, timeout=5000)
    assert window.pages_table.item(0, 1).text() == "Demo App"  # type: ignore[union-attr]

    # Stop recording session
    window.stop_active_session()
    qtbot.waitUntil(lambda: window.status_badge.text() == "Idle", timeout=5000)

    sessions = app_context.session_service.list_sessions(project.id)
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.STOPPED
    pages = app_context.session_service.list_pages(sessions[0].id)
    assert len(pages) == 1
    assert pages[0].closed_at is not None
    # Tab open + tab close events were persisted to raw_events
    assert app_context.event_repository.count_by_session(sessions[0].id) >= 2

    # Process and review the recorded session in the Timeline & Scenarios tab
    assert window.review_widget is not None
    window.review_widget.process_current_session()
    assert window.review_widget.scenarios_list.count() >= 1
    assert window.review_widget.timeline_table.rowCount() >= 1
    window.review_widget.timeline_table.selectRow(0)
    assert "raw_event_ids" in window.review_widget.evidence_inspector.toPlainText()

    # Generate test definitions and preview standalone Pytest code in the Test Definitions tab
    assert window.definitions_widget is not None
    window.definitions_widget.generate_for_current_session()
    assert window.definitions_widget.definitions_list.count() >= 1
    assert window.definitions_widget.actions_table.rowCount() >= 1
    generated_code = window.definitions_widget.code_preview.toPlainText()
    assert "from playwright.sync_api import" in generated_code
    assert "def test_" in generated_code

    # Test Execution tab syncs generated definitions and enables run actions
    assert window.execution_widget is not None
    window.execution_widget.refresh()
    assert window.execution_widget.tests_list.count() >= 1
    assert window.execution_widget.run_selected_btn.isEnabled()
    assert window.execution_widget.run_independent_btn.isEnabled()
    assert window.execution_widget.run_journey_btn.isEnabled()
    assert not window.execution_widget.stop_btn.isEnabled()
