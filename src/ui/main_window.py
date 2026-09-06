"""Main PySide6 desktop window for AutoTest Phase 1."""

from __future__ import annotations

import logging
from contextlib import suppress

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from src.projects import (
    Project,
    ProjectCreate,
    ProjectError,
    ProjectService,
    ProjectUpdate,
    RecordingSession,
    SessionService,
)
from src.ui.dialogs import ProjectDialog
from src.ui.workers import BrowserManagerFactory, BrowserSessionWorker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Primary desktop application shell for project and recording session management."""

    def __init__(
        self,
        settings: AppSettings,
        project_service: ProjectService,
        session_service: SessionService,
        *,
        browser_manager_factory: BrowserManagerFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._project_service = project_service
        self._session_service = session_service
        self._browser_manager_factory = browser_manager_factory

        self._projects: dict[str, Project] = {}
        self._selected_project_id: str | None = None
        self._active_session_id: str | None = None
        self._active_worker: BrowserSessionWorker | None = None
        self._tracked_pages: dict[str, PageInfo] = {}

        self.setWindowTitle(f"AutoTest v{__version__}")
        self.resize(1140, 720)

        self._build_ui()
        self._refresh_projects()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 860])

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

        self.sessions_table = QTableWidget(0, 5, sessions_tab)
        self.sessions_table.setHorizontalHeaderLabels(
            ["Session", "Status", "Start URL", "Started", "Ended"]
        )
        self.sessions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.sessions_table.verticalHeader().setVisible(False)

        sessions_actions = QHBoxLayout()
        sessions_actions.addStretch(1)
        self.delete_session_btn = QPushButton("Delete Selected Session", sessions_tab)
        self.delete_session_btn.clicked.connect(self._on_delete_session)
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

    def _update_project_view(self) -> None:
        has_active_session = self._active_worker is not None
        project = self._projects.get(self._selected_project_id or "")

        self.edit_project_btn.setEnabled(project is not None and not has_active_session)
        self.delete_project_btn.setEnabled(project is not None and not has_active_session)
        self.new_project_btn.setEnabled(not has_active_session)
        self.project_list.setEnabled(not has_active_session)

        if project is None:
            self.project_title_label.setText("No Project Selected")
            self.project_meta_label.setText(
                "Create or select a project on the left to start a recording session."
            )
            self.start_url_input.setText("")
            self.start_url_input.setEnabled(False)
            self.record_toggle_btn.setEnabled(False)
            self.sessions_table.setRowCount(0)
            self.delete_session_btn.setEnabled(False)
            return

        self.project_title_label.setText(project.name)
        meta_parts = [p for p in (project.base_url, project.description) if p]
        self.project_meta_label.setText(" — ".join(meta_parts) if meta_parts else "No details yet.")
        if not has_active_session:
            self.start_url_input.setText(project.base_url)
        self.start_url_input.setEnabled(not has_active_session)
        self.record_toggle_btn.setEnabled(True)
        self._refresh_sessions(project.id)

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
        if self._selected_project_id is None:
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
        self.sessions_table.setRowCount(len(sessions))
        for row_idx, session in enumerate(sessions):
            self._populate_session_row(row_idx, session)
        self.delete_session_btn.setEnabled(bool(sessions) and self._active_worker is None)

    def _populate_session_row(self, row_idx: int, session: RecordingSession) -> None:
        name_item = QTableWidgetItem(session.name)
        name_item.setData(Qt.ItemDataRole.UserRole, session.id)
        started_str = (
            session.started_at.strftime("%Y-%m-%d %H:%M:%S") if session.started_at else "-"
        )
        ended_str = session.ended_at.strftime("%Y-%m-%d %H:%M:%S") if session.ended_at else "-"

        self.sessions_table.setItem(row_idx, 0, name_item)
        self.sessions_table.setItem(row_idx, 1, QTableWidgetItem(session.status.value))
        self.sessions_table.setItem(row_idx, 2, QTableWidgetItem(session.start_url))
        self.sessions_table.setItem(row_idx, 3, QTableWidgetItem(started_str))
        self.sessions_table.setItem(row_idx, 4, QTableWidgetItem(ended_str))

    def _on_delete_session(self) -> None:
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
        self._refresh_sessions(self._selected_project_id)

    def _on_toggle_recording(self) -> None:
        if self._active_worker is not None:
            self.stop_active_session()
        else:
            self.start_recording_session()

    def start_recording_session(self) -> None:
        """Create and start a recording session and launch dedicated Chromium off-thread."""
        if self._selected_project_id is None or self._active_worker is not None:
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

        config = BrowserLaunchConfig.from_settings(self._settings, start_url=session.start_url)
        worker = BrowserSessionWorker(
            session.id,
            config,
            manager_factory=self._browser_manager_factory,
            parent=self,
        )
        worker.session_started.connect(self._on_worker_started)
        worker.page_opened.connect(self._on_worker_page_opened)
        worker.page_navigated.connect(self._on_worker_page_navigated)
        worker.page_closed.connect(self._on_worker_page_closed)
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
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage("Chromium running — recording session active.")

    def _on_worker_page_opened(self, session_id: str, info_obj: object) -> None:
        if not isinstance(info_obj, PageInfo):
            return
        self._tracked_pages[info_obj.page_id] = info_obj
        self._session_service.register_page(
            session_id,
            page_id=info_obj.page_id,
            tab_index=info_obj.tab_index,
            url=info_obj.url,
            title=info_obj.title,
        )
        self._refresh_pages_table()

    def _on_worker_page_navigated(self, _session_id: str, info_obj: object) -> None:
        if not isinstance(info_obj, PageInfo):
            return
        self._tracked_pages[info_obj.page_id] = info_obj
        self._session_service.update_page(
            info_obj.page_id,
            url=info_obj.url,
            title=info_obj.title,
        )
        self._refresh_pages_table()

    def _on_worker_page_closed(self, _session_id: str, info_obj: object) -> None:
        if not isinstance(info_obj, PageInfo):
            return
        self._tracked_pages[info_obj.page_id] = info_obj
        self._session_service.close_page(info_obj.page_id)
        self._refresh_pages_table()

    def _on_worker_finished(self, session_id: str) -> None:
        try:
            self._session_service.stop_session(session_id)
        except ProjectError:
            logger.debug("Session %s already in terminal state", session_id)
        self._cleanup_worker()
        self._set_status_badge("Idle", "idle")
        self.statusBar().showMessage("Recording session stopped.", 5000)

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
        """Ensure any active browser worker stops cleanly before exiting the window."""
        if self._active_worker is not None:
            self._active_worker.request_stop()
            self._active_worker.wait(5000)
            if self._active_session_id is not None:
                with suppress(ProjectError):
                    self._session_service.stop_session(self._active_session_id)
            self._active_worker = None
            self._active_session_id = None
        super().closeEvent(event)
