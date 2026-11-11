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
    qtbot.waitUntil(
        lambda: (
            window.review_widget is not None
            and not window.review_widget.is_processing
            and window.review_widget.scenarios_list.count() >= 1
        ),
        timeout=5000,
    )
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

    # Test Execution tab auto-syncs generated definitions without manual refresh()
    assert window.execution_widget is not None
    assert window.execution_widget.tests_list.count() >= 1
    assert window.execution_widget.run_selected_btn.isEnabled()
    assert window.execution_widget.run_independent_btn.isEnabled()
    assert window.execution_widget.run_journey_btn.isEnabled()
    assert not window.execution_widget.stop_btn.isEnabled()

    # Active execution locks project/session deletion & prevents load_session clobbering
    active_sid = window.execution_widget._session_id
    window.execution_widget._worker = object()  # type: ignore[assignment]
    window.execution_widget._active_run_session_id = active_sid
    window.execution_widget.execution_started.emit()
    assert not window.delete_project_btn.isEnabled()
    assert not window.delete_session_btn.isEnabled()
    assert not window.record_toggle_btn.isEnabled()
    window.execution_widget.load_session("other-session-id")
    assert window.execution_widget._session_id == active_sid
    window.execution_widget._worker = None
    window.execution_widget._active_run_session_id = None
    window.execution_widget.execution_finished.emit()
    assert window.delete_project_btn.isEnabled()
    assert window.record_toggle_btn.isEnabled()


@pytest.mark.ui
def test_project_switching_clears_tabs_and_session_reloads_pages(
    qtbot: QtBot,
    app_context: AppContext,
) -> None:
    p1 = app_context.project_service.create_project(
        ProjectCreate(name="Alpha App", base_url="https://alpha.example.com")
    )
    p2 = app_context.project_service.create_project(
        ProjectCreate(name="Beta App", base_url="https://beta.example.com")
    )
    s1 = app_context.session_service.create_session(p1.id, name="Alpha Session")
    app_context.session_service.register_page(
        s1.id,
        page_id="pg-alpha-1",
        tab_index=0,
        url="https://alpha.example.com/home",
        title="Alpha Home",
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

    # Select Alpha App (find its row)
    for idx in range(window.project_list.count()):
        item = window.project_list.item(idx)
        if item is not None and item.text() == "Alpha App":
            window.project_list.setCurrentRow(idx)
            break

    # Session auto-selected -> pages_table reloaded from persisted browser_pages
    assert window.pages_table.rowCount() == 1
    assert window.pages_table.item(0, 1).text() == "Alpha Home"  # type: ignore[union-attr]

    # Switch to Beta App (no sessions) -> all tabs cleared cleanly
    for idx in range(window.project_list.count()):
        item = window.project_list.item(idx)
        if item is not None and item.text() == "Beta App":
            window.project_list.setCurrentRow(idx)
            break

    assert window.project_title_label.text() == "Beta App"
    assert window.sessions_table.rowCount() == 0
    assert window.pages_table.rowCount() == 0
    assert window.events_table.rowCount() == 0
    assert window.execution_widget is not None
    assert window.execution_widget.tests_list.count() == 0
    assert p2.name == "Beta App"

    # High #18: Switching between two projects that BOTH have a session at Row 0 populates tabs
    s2 = app_context.session_service.create_session(p2.id, name="Beta Session")
    app_context.session_service.register_page(
        s2.id,
        page_id="pg-beta-1",
        tab_index=0,
        url="https://beta.example.com/dash",
        title="Beta Dash",
    )
    for idx in range(window.project_list.count()):
        item = window.project_list.item(idx)
        if item is not None and item.text() == "Alpha App":
            window.project_list.setCurrentRow(idx)
            break
    assert window.pages_table.rowCount() == 1
    assert window.pages_table.item(0, 1).text() == "Alpha Home"  # type: ignore[union-attr]

    for idx in range(window.project_list.count()):
        item = window.project_list.item(idx)
        if item is not None and item.text() == "Beta App":
            window.project_list.setCurrentRow(idx)
            break
    assert window.pages_table.rowCount() == 1
    assert window.pages_table.item(0, 1).text() == "Beta Dash"  # type: ignore[union-attr]

    # High #17: External busy lock disables sibling tab controls
    assert window.review_widget is not None
    assert window.definitions_widget is not None
    window.review_widget.set_external_busy(True)
    assert not window.review_widget.process_btn.isEnabled()
    window.review_widget.set_external_busy(False)
    assert window.review_widget.process_btn.isEnabled()
    window.definitions_widget.set_external_busy(True)
    assert not window.definitions_widget.generate_btn.isEnabled()
    window.definitions_widget.set_external_busy(False)
    assert window.definitions_widget.generate_btn.isEnabled()

    # Execution/processing state updates preserve session selection and custom Start URL
    assert window.sessions_table.currentRow() == 0
    window.start_url_input.setText("https://beta.example.com/custom-path")
    window._on_execution_state_changed()
    assert window.sessions_table.currentRow() == 0
    assert window.start_url_input.text() == "https://beta.example.com/custom-path"
