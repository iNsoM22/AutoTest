"""Main PySide6 desktop window for AutoTest."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src import __version__
from src.browser import BrowserLaunchConfig, PageInfo
from src.config import AppSettings
from src.definitions import TestDefinitionService
from src.execution import ExecutionService
from src.export import CopyExportService, RecordingBundleError, RecordingBundleService
from src.projects import (
    Project,
    ProjectCreate,
    ProjectError,
    ProjectService,
    ProjectUpdate,
    RecordingSession,
    SessionService,
)
from src.recording import RawEvent
from src.scenarios import ScenarioService
from src.storage import ArtifactManager, ArtifactRepository, RawEventRepository
from src.ui.definitions_widget import DefinitionsWidget
from src.ui.dialogs import ProjectDialog
from src.ui.execution_widget import ExecutionWidget
from src.ui.review_widget import ReviewWidget
from src.ui.workers import BrowserManagerFactory, BrowserSessionWorker
from src.utils import target_label

logger = logging.getLogger(__name__)

_MAX_VISIBLE_EVENTS = 500


class MainWindow(QMainWindow):
    """Primary desktop application shell for projects, sessions, and live recordings."""

    def __init__(
        self,
        settings: AppSettings,
        project_service: ProjectService,
        session_service: SessionService,
        event_repository: RawEventRepository,
        *,
        scenario_service: ScenarioService | None = None,
        definition_service: TestDefinitionService | None = None,
        copy_service: CopyExportService | None = None,
        execution_service: ExecutionService | None = None,
        artifacts: ArtifactManager | None = None,
        artifact_repository: ArtifactRepository | None = None,
        recording_bundle_service: RecordingBundleService | None = None,
        browser_manager_factory: BrowserManagerFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._project_service = project_service
        self._session_service = session_service
        self._event_repository = event_repository
        self._scenario_service = scenario_service
        self._definition_service = definition_service
        self._copy_service = copy_service
        self._execution_service = execution_service
        self._artifacts = artifacts
        self._artifact_repository = artifact_repository
        self._recording_bundle_service = recording_bundle_service
        self._browser_manager_factory = browser_manager_factory
        self.review_widget: ReviewWidget | None = None
        self.definitions_widget: DefinitionsWidget | None = None
        self.execution_widget: ExecutionWidget | None = None

        self._projects: dict[str, Project] = {}
        self._selected_project_id: str | None = None
        self._loaded_project_id: str | None = None
        self._active_session_id: str | None = None
        self._active_worker: BrowserSessionWorker | None = None
        self._tracked_pages: dict[str, PageInfo] = {}
        self._event_count: int = 0

        self.setWindowTitle(f"AutoTest v{__version__}")
        self.resize(1180, 740)

        self._build_ui()
        self._refresh_projects()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 900])

        self.setCentralWidget(splitter)

        status_bar = QStatusBar(self)
        status_bar.showMessage(f"Ready | Database: {self._settings.db_path}")
        self.setStatusBar(status_bar)

    def _build_sidebar(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 10, 14)
        layout.setSpacing(10)

        title = QLabel("Projects", panel)
        title.setObjectName("sectionTitle")

        self.project_list = QListWidget(panel)
        self.project_list.currentItemChanged.connect(self._on_project_selection_changed)

        btn_row = QHBoxLayout()
        self.new_project_btn = QPushButton("+ New", panel)
        self.new_project_btn.setObjectName("primaryButton")
        self.new_project_btn.clicked.connect(self._on_new_project)

        self.edit_project_btn = QPushButton("Edit", panel)
        self.edit_project_btn.clicked.connect(self._on_edit_project)

        self.delete_project_btn = QPushButton("Delete", panel)
        self.delete_project_btn.clicked.connect(self._on_delete_project)

        btn_row.addWidget(self.new_project_btn)
        btn_row.addWidget(self.edit_project_btn)
        btn_row.addWidget(self.delete_project_btn)

        layout.addWidget(title)
        layout.addWidget(self.project_list, stretch=1)
        layout.addLayout(btn_row)
        return panel

    def _build_workspace(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # Header card
        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        self.project_title_label = QLabel("No Project Selected", panel)
        self.project_title_label.setObjectName("projectTitle")
        self.project_meta_label = QLabel(
            "Create or select a project on the left to start a recording session.",
            panel,
        )
        self.project_meta_label.setObjectName("mutedLabel")
        self.project_meta_label.setWordWrap(True)
        header_text.addWidget(self.project_title_label)
        header_text.addWidget(self.project_meta_label)

        self.status_badge = QLabel("Idle", panel)
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setProperty("state", "idle")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_badge.setFixedHeight(28)

        header_row.addLayout(header_text, stretch=1)
        header_row.addWidget(self.status_badge, alignment=Qt.AlignmentFlag.AlignTop)

        # Divider
        divider = QFrame(panel)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #2e3440;")

        # Session control bar
        control_row = QHBoxLayout()
        url_label = QLabel("Start URL:", panel)
        self.start_url_input = QLineEdit(panel)
        self.start_url_input.setPlaceholderText("https://example.com")

        self.record_toggle_btn = QPushButton("Start Recording", panel)
        self.record_toggle_btn.setObjectName("primaryButton")
        self.record_toggle_btn.setMinimumWidth(148)
        self.record_toggle_btn.clicked.connect(self._on_toggle_recording)

        control_row.addWidget(url_label)
        control_row.addWidget(self.start_url_input, stretch=1)
        control_row.addWidget(self.record_toggle_btn)

        # Workspace tabs
        self.tabs = QTabWidget(panel)

        # Tab 1: Sessions table
        sessions_tab = QWidget(panel)
        sessions_layout = QVBoxLayout(sessions_tab)
        sessions_layout.setContentsMargins(10, 10, 10, 10)
        sessions_layout.setSpacing(8)

        self.sessions_table = QTableWidget(0, 6, sessions_tab)
        self.sessions_table.setHorizontalHeaderLabels(
            ["Session", "Status", "Events", "Start URL", "Started", "Ended"]
        )
        self.sessions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.sessions_table.verticalHeader().setVisible(False)
        self.sessions_table.itemSelectionChanged.connect(self._on_session_selection_changed)

        sessions_actions = QHBoxLayout()
        self.import_recording_btn = QPushButton("Import Recording (JSON)", sessions_tab)
        self.import_recording_btn.clicked.connect(self._on_import_recording)
        sessions_actions.addWidget(self.import_recording_btn)
        sessions_actions.addStretch(1)
        self.review_session_btn = QPushButton("Process & Review", sessions_tab)
        self.review_session_btn.setObjectName("primaryButton")
        self.review_session_btn.clicked.connect(self._on_review_selected_session)
        self.delete_session_btn = QPushButton("Delete Selected Session", sessions_tab)
        self.delete_session_btn.clicked.connect(self._on_delete_session)
        sessions_actions.addWidget(self.review_session_btn)
        sessions_actions.addWidget(self.delete_session_btn)

        sessions_layout.addWidget(self.sessions_table, stretch=1)
        sessions_layout.addLayout(sessions_actions)
        self.tabs.addTab(sessions_tab, "Recording Sessions")

        # Tab 2: Tracked browser tabs
        pages_tab = QWidget(panel)
        pages_layout = QVBoxLayout(pages_tab)
        pages_layout.setContentsMargins(10, 10, 10, 10)

        self.pages_table = QTableWidget(0, 4, pages_tab)
        self.pages_table.setHorizontalHeaderLabels(["Tab #", "Title", "URL", "State"])
        self.pages_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pages_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.pages_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pages_table.verticalHeader().setVisible(False)
        pages_layout.addWidget(self.pages_table)
        self.tabs.addTab(pages_tab, "Browser Tabs")

        # Tab 3: Recorded raw events
        events_tab = QWidget(panel)
        events_layout = QVBoxLayout(events_tab)
        events_layout.setContentsMargins(10, 10, 10, 10)

        self.events_table = QTableWidget(0, 5, events_tab)
        self.events_table.setHorizontalHeaderLabels(
            ["#", "Event Type", "Source", "Target / Details", "Timestamp"]
        )
        self.events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.events_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.events_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.events_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.events_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.events_table.verticalHeader().setVisible(False)
        events_layout.addWidget(self.events_table)
        self.tabs.addTab(events_tab, "Recorded Events (0)")

        # Tab 4: Timeline & Scenarios review
        if self._scenario_service is not None:
            self.review_widget = ReviewWidget(
                self._scenario_service,
                panel,
                artifacts=self._artifacts,
                artifact_repository=self._artifact_repository,
                recording_bundle_service=self._recording_bundle_service,
                session_service=self._session_service,
            )
            self.review_widget.scenarios_changed.connect(self._on_scenarios_changed)
            self.review_widget.processing_started.connect(self._on_execution_state_changed)
            self.review_widget.processing_finished.connect(self._on_execution_state_changed)
            self.tabs.addTab(self.review_widget, "Timeline & Scenarios")

        # Tab 5: Test Definitions & Generated Code
        if self._definition_service is not None:
            self.definitions_widget = DefinitionsWidget(
                self._definition_service,
                self._copy_service,
                panel,
            )
            self.definitions_widget.definitions_changed.connect(self._on_definitions_changed)
            self.tabs.addTab(self.definitions_widget, "Test Definitions & Code")

        # Tab 6: Built-in Test Execution
        if self._execution_service is not None and self._definition_service is not None:
            self.execution_widget = ExecutionWidget(
                self._execution_service,
                self._definition_service,
                panel,
            )
            self.execution_widget.execution_started.connect(self._on_execution_state_changed)
            self.execution_widget.execution_finished.connect(self._on_execution_state_changed)
            self.tabs.addTab(self.execution_widget, "Test Execution")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addLayout(header_row)
        layout.addWidget(divider)
        layout.addLayout(control_row)
        layout.addWidget(self.tabs, stretch=1)
        return panel

    # --- Project management handlers ---

    def _refresh_projects(self, select_project_id: str | None = None) -> None:
        target_id = select_project_id or self._selected_project_id
        projects = self._project_service.list_projects()
        self._projects = {p.id: p for p in projects}

        self.project_list.blockSignals(True)
        self.project_list.clear()
        selected_row = -1
        for idx, project in enumerate(projects):
            item = QListWidgetItem(project.name)
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            self.project_list.addItem(item)
            if project.id == target_id:
                selected_row = idx

        if selected_row == -1 and projects:
            selected_row = 0

        if selected_row >= 0:
            self.project_list.setCurrentRow(selected_row)
            self._selected_project_id = projects[selected_row].id
        else:
            self._selected_project_id = None
        self.project_list.blockSignals(False)

        self._update_project_view()

    def _on_project_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self._selected_project_id = (
            str(current.data(Qt.ItemDataRole.UserRole)) if current is not None else None
        )
        self._update_project_view()

    def _is_execution_running(self) -> bool:
        return self.execution_widget is not None and self.execution_widget.is_running

    def _is_processing(self) -> bool:
        return self.review_widget is not None and self.review_widget.is_processing

    def _is_busy(self) -> bool:
        return (
            self._active_worker is not None or self._is_execution_running() or self._is_processing()
        )

    def _on_scenarios_changed(self, session_id: str) -> None:
        if self.definitions_widget is not None:
            self.definitions_widget.refresh(session_id)
        if self.execution_widget is not None and not self.execution_widget.is_running:
            self.execution_widget.refresh()

    def _on_definitions_changed(self, _session_id: str) -> None:
        if self.execution_widget is not None and not self.execution_widget.is_running:
            self.execution_widget.refresh()

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if (
            self.execution_widget is not None
            and widget is self.execution_widget
            and not self.execution_widget.is_running
        ):
            self.execution_widget.refresh()

    def _on_execution_state_changed(self) -> None:
        self._sync_busy_controls()

    def _sync_busy_controls(self) -> None:
        has_active_session = self._active_worker is not None
        is_busy = self._is_busy()
        self._sync_tab_locks()
        has_project = self._projects.get(self._selected_project_id or "") is not None
        has_sessions = self.sessions_table.rowCount() > 0

        self.edit_project_btn.setEnabled(has_project and not is_busy)
        self.delete_project_btn.setEnabled(has_project and not is_busy)
        self.new_project_btn.setEnabled(not is_busy)
        self.project_list.setEnabled(not is_busy)
        self.start_url_input.setEnabled(has_project and not is_busy)
        self.record_toggle_btn.setEnabled(
            has_project
            and (has_active_session or not (self._is_execution_running() or self._is_processing()))
        )
        self.delete_session_btn.setEnabled(has_sessions and not is_busy)
        self.review_session_btn.setEnabled(
            has_sessions and not is_busy and self.review_widget is not None
        )
        self.import_recording_btn.setEnabled(
            has_project and not is_busy and self._recording_bundle_service is not None
        )

    def _sync_tab_locks(self) -> None:
        recording = self._active_worker is not None
        executing = self._is_execution_running()
        processing = self._is_processing()
        if self.review_widget is not None:
            self.review_widget.set_external_busy(recording or executing)
        if self.definitions_widget is not None:
            self.definitions_widget.set_external_busy(recording or executing or processing)
        if self.execution_widget is not None:
            self.execution_widget.set_external_busy(recording or processing)

    def _update_project_view(self) -> None:
        has_active_session = self._active_worker is not None
        is_busy = self._is_busy()
        self._sync_tab_locks()
        project = self._projects.get(self._selected_project_id or "")

        self.edit_project_btn.setEnabled(project is not None and not is_busy)
        self.delete_project_btn.setEnabled(project is not None and not is_busy)
        self.new_project_btn.setEnabled(not is_busy)
        self.project_list.setEnabled(not is_busy)

        if project is None:
            self._loaded_project_id = None
            self.project_title_label.setText("No Project Selected")
            self.project_meta_label.setText(
                "Create or select a project on the left to start a recording session."
            )
            self.start_url_input.setText("")
            self.start_url_input.setEnabled(False)
            self.record_toggle_btn.setEnabled(False)
            self.sessions_table.setRowCount(0)
            self.delete_session_btn.setEnabled(False)
            self.review_session_btn.setEnabled(False)
            self.import_recording_btn.setEnabled(False)
            self._clear_session_tabs()
            return

        project_changed = project.id != self._loaded_project_id
        self._loaded_project_id = project.id
        if project_changed and not is_busy:
            self._clear_session_tabs()

        self.project_title_label.setText(project.name)
        if self.definitions_widget is not None:
            self.definitions_widget.set_base_url(project.base_url)
        meta_parts = [p for p in (project.base_url, project.description) if p]
        self.project_meta_label.setText(" — ".join(meta_parts) if meta_parts else "No details yet.")
        if not is_busy:
            self.start_url_input.setText(project.base_url)
        self.start_url_input.setEnabled(not is_busy)
        self.record_toggle_btn.setEnabled(
            has_active_session or not (self._is_execution_running() or self._is_processing())
        )
        self.import_recording_btn.setEnabled(
            not is_busy and self._recording_bundle_service is not None
        )
        self._refresh_sessions(project.id)
        if project_changed and not is_busy and self.sessions_table.rowCount() > 0:
            self.sessions_table.selectRow(0)

    def _on_new_project(self) -> None:
        dialog = ProjectDialog(parent=self)
        if dialog.exec() != ProjectDialog.DialogCode.Accepted:
            return
        name, base_url, description = dialog.get_values()
        try:
            created = self._project_service.create_project(
                ProjectCreate(name=name, base_url=base_url, description=description)
            )
        except (ProjectError, ValueError) as exc:
            QMessageBox.warning(self, "Could Not Create Project", str(exc))
            return

        self._refresh_projects(select_project_id=created.id)
        self.statusBar().showMessage(f"Created project '{created.name}'.", 4000)

    def _on_edit_project(self) -> None:
        if self._selected_project_id is None:
            return
        project = self._projects.get(self._selected_project_id)
        if project is None:
            return

        dialog = ProjectDialog(project=project, parent=self)
        if dialog.exec() != ProjectDialog.DialogCode.Accepted:
            return
        name, base_url, description = dialog.get_values()
        try:
            updated = self._project_service.update_project(
                project.id,
                ProjectUpdate(name=name, base_url=base_url, description=description),
            )
        except (ProjectError, ValueError) as exc:
            QMessageBox.warning(self, "Could Not Update Project", str(exc))
            return

        self._refresh_projects(select_project_id=updated.id)
        self.statusBar().showMessage(f"Updated project '{updated.name}'.", 4000)

    def _on_delete_project(self) -> None:
        if self._selected_project_id is None or self._is_busy():
            return
        project = self._projects.get(self._selected_project_id)
        if project is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete Project",
            f"Delete project '{project.name}' and all of its recording sessions?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self._project_service.delete_project(project.id)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could Not Delete Project", str(exc))
            return

        self._selected_project_id = None
        self._refresh_projects()
        self.statusBar().showMessage(f"Deleted project '{project.name}'.", 4000)

    # --- Recording session lifecycle handlers ---

    def _refresh_sessions(self, project_id: str) -> None:
        sessions = self._session_service.list_sessions(project_id)
        self.sessions_table.blockSignals(True)
        self.sessions_table.clearSelection()
        self.sessions_table.setCurrentCell(-1, -1)
        self.sessions_table.setRowCount(len(sessions))
        for row_idx, session in enumerate(sessions):
            self._populate_session_row(row_idx, session)
        self.sessions_table.blockSignals(False)
        is_busy = self._is_busy()
        self.delete_session_btn.setEnabled(bool(sessions) and not is_busy)
        self.review_session_btn.setEnabled(
            bool(sessions) and not is_busy and self.review_widget is not None
        )

    def _populate_session_row(self, row_idx: int, session: RecordingSession) -> None:
        name_item = QTableWidgetItem(session.name)
        name_item.setData(Qt.ItemDataRole.UserRole, session.id)
        event_count = self._event_repository.count_by_session(session.id)
        started_str = (
            session.started_at.strftime("%Y-%m-%d %H:%M:%S") if session.started_at else "-"
        )
        ended_str = session.ended_at.strftime("%Y-%m-%d %H:%M:%S") if session.ended_at else "-"

        self.sessions_table.setItem(row_idx, 0, name_item)
        self.sessions_table.setItem(row_idx, 1, QTableWidgetItem(session.status.value))
        self.sessions_table.setItem(row_idx, 2, QTableWidgetItem(str(event_count)))
        self.sessions_table.setItem(row_idx, 3, QTableWidgetItem(session.start_url))
        self.sessions_table.setItem(row_idx, 4, QTableWidgetItem(started_str))
        self.sessions_table.setItem(row_idx, 5, QTableWidgetItem(ended_str))

    def _on_session_selection_changed(self) -> None:
        if self._is_busy():
            return
        row = self.sessions_table.currentRow()
        if row < 0:
            return
        item = self.sessions_table.item(row, 0)
        if item is None:
            return
        session_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._load_session_pages(session_id)
        self._load_session_events(session_id)
        self._bind_session_tabs(session_id, item.text())

    def _on_review_selected_session(self) -> None:
        if self.review_widget is None:
            return
        row = self.sessions_table.currentRow()
        if row < 0 and self.sessions_table.rowCount() > 0:
            row = 0
            self.sessions_table.selectRow(0)
        if row < 0:
            return
        item = self.sessions_table.item(row, 0)
        if item is None:
            return
        session_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._bind_session_tabs(session_id, item.text())
        self.review_widget.process_current_session()
        self.tabs.setCurrentWidget(self.review_widget)

    def _on_delete_session(self) -> None:
        if self._is_busy():
            return
        row = self.sessions_table.currentRow()
        if row < 0 or self._selected_project_id is None:
            return
        item = self.sessions_table.item(row, 0)
        if item is None:
            return
        session_id = str(item.data(Qt.ItemDataRole.UserRole))
        try:
            self._session_service.delete_session(session_id)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could Not Delete Session", str(exc))
            return
        self._clear_session_tabs()
        self._refresh_sessions(self._selected_project_id)
        if self.sessions_table.rowCount() > 0:
            self.sessions_table.selectRow(0)

    def _on_import_recording(self) -> None:
        if (
            self._selected_project_id is None
            or self._recording_bundle_service is None
            or self._is_busy()
        ):
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Processed Recording (JSON)",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return
        try:
            imported = self._recording_bundle_service.import_from_file(
                self._selected_project_id,
                Path(file_path),
            )
        except (RecordingBundleError, OSError) as exc:
            QMessageBox.warning(self, "Import Failed", str(exc))
            return

        self._refresh_sessions(self._selected_project_id)
        for row_idx in range(self.sessions_table.rowCount()):
            item = self.sessions_table.item(row_idx, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == imported.id:
                self.sessions_table.selectRow(row_idx)
                break
        if self.review_widget is not None:
            self.tabs.setCurrentWidget(self.review_widget)
        self.statusBar().showMessage(f"Imported processed recording '{imported.name}'.", 5000)

    def _on_toggle_recording(self) -> None:
        if self._active_worker is not None:
            self.stop_active_session()
        else:
            self.start_recording_session()

    def start_recording_session(self) -> None:
        """Create and start a recording session and launch dedicated Chromium off-thread."""
        if self._selected_project_id is None or self._is_busy():
            return

        start_url = self.start_url_input.text().strip()
        try:
            session = self._session_service.create_session(
                self._selected_project_id,
                start_url=start_url,
            )
            session = self._session_service.start_session(session.id)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could Not Start Session", str(exc))
            return

        self._active_session_id = session.id
        self._tracked_pages.clear()
        self._refresh_pages_table()
        self._clear_events_table()

        video_dir_str = ""
        if self._artifacts is not None:
            video_dir = (
                self._artifacts.ensure_session_dir(self._selected_project_id, session.id) / "video"
            )
            video_dir.mkdir(parents=True, exist_ok=True)
            video_dir_str = str(video_dir)

        config = BrowserLaunchConfig.from_settings(
            self._settings,
            start_url=session.start_url,
            record_video_dir=video_dir_str,
        )
        worker = BrowserSessionWorker(
            self._selected_project_id,
            session.id,
            config,
            self._session_service,
            self._event_repository,
            batch_size=self._settings.event_batch_size,
            artifacts=self._artifacts,
            artifact_repository=self._artifact_repository,
            manager_factory=self._browser_manager_factory,
            parent=self,
        )
        worker.session_started.connect(self._on_worker_started)
        worker.page_opened.connect(self._on_worker_page_updated)
        worker.page_navigated.connect(self._on_worker_page_updated)
        worker.page_closed.connect(self._on_worker_page_updated)
        worker.events_recorded.connect(self._on_worker_events_recorded)
        worker.session_finished.connect(self._on_worker_finished)
        worker.session_failed.connect(self._on_worker_failed)

        self._active_worker = worker
        self._set_status_badge("Launching...", "launching")
        self.record_toggle_btn.setText("Stop Recording")
        self.record_toggle_btn.setObjectName("dangerButton")
        self._repolish(self.record_toggle_btn)
        self._update_project_view()

        self.statusBar().showMessage(
            f"Launching dedicated Chromium for session '{session.name}'..."
        )
        worker.start()

    def stop_active_session(self) -> None:
        """Request graceful stop of the active browser session worker."""
        if self._active_worker is None:
            return
        self._set_status_badge("Stopping...", "launching")
        self.record_toggle_btn.setEnabled(False)
        self.statusBar().showMessage("Stopping recording session...")
        self._active_worker.request_stop()

    def _on_worker_started(self, session_id: str) -> None:
        if session_id != self._active_session_id:
            return
        self._set_status_badge("Recording Active", "active")
        self.tabs.setCurrentIndex(2)
        self.statusBar().showMessage("Chromium running — recording session active.")

    def _on_worker_page_updated(self, session_id: str, info_obj: object) -> None:
        if session_id != self._active_session_id or not isinstance(info_obj, PageInfo):
            return
        self._tracked_pages[info_obj.page_id] = info_obj
        self._refresh_pages_table()

    def _on_worker_events_recorded(
        self,
        session_id: str,
        total_count: int,
        batch_obj: object,
    ) -> None:
        if session_id != self._active_session_id or not isinstance(batch_obj, list):
            return
        self._event_count = total_count
        self.tabs.setTabText(2, f"Recorded Events ({total_count})")
        for event in batch_obj:
            if isinstance(event, RawEvent):
                self._append_event_row(event)

    def _on_worker_finished(self, session_id: str) -> None:
        try:
            self._session_service.stop_session(session_id)
        except ProjectError:
            logger.debug("Session %s already in terminal state", session_id)
        self._cleanup_worker()
        session_name = ""
        for row_idx in range(self.sessions_table.rowCount()):
            item = self.sessions_table.item(row_idx, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == session_id:
                session_name = item.text()
                self.sessions_table.blockSignals(True)
                self.sessions_table.selectRow(row_idx)
                self.sessions_table.blockSignals(False)
                break
        self._bind_session_tabs(session_id, session_name)
        if (
            self.review_widget is not None
            and self._event_repository.count_by_session(session_id) > 0
        ):
            self.review_widget.process_current_session()
        self._set_status_badge("Idle", "idle")
        self.statusBar().showMessage(
            f"Recording session stopped ({self._event_count} events captured).",
            5000,
        )

    def _on_worker_failed(self, session_id: str, error_message: str) -> None:
        try:
            self._session_service.fail_session(session_id, error_message)
        except ProjectError:
            logger.debug("Could not mark session %s as failed", session_id)
        self._cleanup_worker()
        self._set_status_badge("Error", "error")
        self.statusBar().showMessage(f"Session failed: {error_message}")
        QMessageBox.critical(self, "Browser Session Error", error_message)

    def _cleanup_worker(self) -> None:
        worker = self._active_worker
        self._active_worker = None
        self._active_session_id = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()

        self.record_toggle_btn.setText("Start Recording")
        self.record_toggle_btn.setObjectName("primaryButton")
        self._repolish(self.record_toggle_btn)
        self._update_project_view()

    def _refresh_pages_table(self) -> None:
        pages = sorted(self._tracked_pages.values(), key=lambda p: p.tab_index)
        self.pages_table.setRowCount(len(pages))
        for row_idx, page in enumerate(pages):
            self.pages_table.setItem(row_idx, 0, QTableWidgetItem(str(page.tab_index + 1)))
            self.pages_table.setItem(row_idx, 1, QTableWidgetItem(page.title or "(untitled)"))
            self.pages_table.setItem(row_idx, 2, QTableWidgetItem(page.url))
            self.pages_table.setItem(
                row_idx, 3, QTableWidgetItem("Closed" if page.is_closed else "Open")
            )

    def _load_session_pages(self, session_id: str) -> None:
        pages = self._session_service.list_pages(session_id)
        self._tracked_pages = {
            page.id: PageInfo(
                page_id=page.id,
                tab_index=page.tab_index,
                url=page.last_url or page.initial_url,
                title=page.title,
                is_closed=page.closed_at is not None,
            )
            for page in pages
        }
        self._refresh_pages_table()

    def _bind_session_tabs(self, session_id: str | None, session_name: str = "") -> None:
        if self.review_widget is not None:
            self.review_widget.load_session(session_id, session_name)
        if self.definitions_widget is not None:
            project = self._projects.get(self._selected_project_id or "")
            self.definitions_widget.load_session(
                session_id,
                session_name,
                base_url=project.base_url if project is not None else "",
            )
        if self.execution_widget is not None and not self.execution_widget.is_running:
            self.execution_widget.load_session(session_id, session_name)

    def _clear_session_tabs(self) -> None:
        self._tracked_pages.clear()
        self._refresh_pages_table()
        self._clear_events_table()
        self._bind_session_tabs(None)

    def _clear_events_table(self) -> None:
        self._event_count = 0
        self.events_table.setRowCount(0)
        self.tabs.setTabText(2, "Recorded Events (0)")

    def _load_session_events(self, session_id: str) -> None:
        total = self._event_repository.count_by_session(session_id)
        events = self._event_repository.list_by_session(session_id, limit=_MAX_VISIBLE_EVENTS)
        self._event_count = total
        self.events_table.setRowCount(0)
        self.tabs.setTabText(2, f"Recorded Events ({total})")
        for event in events:
            self._append_event_row(event)

    def _append_event_row(self, event: RawEvent) -> None:
        if self.events_table.rowCount() >= _MAX_VISIBLE_EVENTS:
            self.events_table.removeRow(0)
        row_idx = self.events_table.rowCount()
        self.events_table.insertRow(row_idx)
        self.events_table.setItem(row_idx, 0, QTableWidgetItem(str(event.sequence_no)))
        self.events_table.setItem(row_idx, 1, QTableWidgetItem(event.event_type.value))
        self.events_table.setItem(row_idx, 2, QTableWidgetItem(event.source.value))
        self.events_table.setItem(row_idx, 3, QTableWidgetItem(self._summarize_event(event)))
        self.events_table.setItem(
            row_idx, 4, QTableWidgetItem(event.timestamp.strftime("%H:%M:%S.%f")[:-3])
        )

    @staticmethod
    def _summarize_event(event: RawEvent) -> str:
        target = event.target
        payload = event.payload
        if target:
            label = target_label(target, default="")
            value = payload.get("value")
            if value is not None and value != "":
                return f"{label} = {value}"
            if "key" in payload:
                return f"{label} [{payload['key']}]"
            return label
        if "status" in payload:
            return f"{payload.get('method', '')} {payload.get('status')} {payload.get('url', '')}"
        if "url" in payload:
            method = payload.get("method", "")
            prefix = f"{method} " if method else ""
            return f"{prefix}{payload['url']}"
        if "text" in payload:
            return f"[{payload.get('level', 'log')}] {payload['text']}"
        if "message" in payload:
            return str(payload["message"])
        return ""

    def _set_status_badge(self, text: str, state: str) -> None:
        self.status_badge.setText(text)
        self.status_badge.setProperty("state", state)
        self._repolish(self.status_badge)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure any active browser, review, or execution worker stops cleanly before exiting."""
        worker = self._active_worker
        if worker is not None:
            for sig in (
                worker.session_started,
                worker.page_opened,
                worker.page_navigated,
                worker.page_closed,
                worker.events_recorded,
                worker.session_finished,
                worker.session_failed,
            ):
                with suppress(RuntimeError, TypeError):
                    sig.disconnect()
            worker.request_stop()
            worker.wait(5000)
            if not worker.isRunning():
                worker.deleteLater()
            else:
                worker.setParent(None)
            if self._active_session_id is not None:
                with suppress(ProjectError):
                    self._session_service.stop_session(self._active_session_id)
            self._active_worker = None
            self._active_session_id = None
        if self.review_widget is not None:
            self.review_widget.shutdown()
        if self.execution_widget is not None:
            self.execution_widget.shutdown()
        super().closeEvent(event)
