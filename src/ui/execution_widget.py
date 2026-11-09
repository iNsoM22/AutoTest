"""Built-in Pytest execution tab with live progress, failure diagnostics, and history."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.definitions import TestDefinition, TestDefinitionService, TestMode
from src.execution import (
    ExecutionError,
    ExecutionMode,
    ExecutionProgressEvent,
    ExecutionService,
    TestCaseResult,
    TestRun,
)
from src.ui.workers import TestExecutionWorker


class ExecutionWidget(QWidget):
    """Desktop workspace tab for running tests, viewing live output, and inspecting history."""

    execution_started = Signal()
    execution_finished = Signal()

    def __init__(
        self,
        execution_service: ExecutionService,
        definition_service: TestDefinitionService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._execution_service = execution_service
        self._definition_service = definition_service

        self._session_id: str | None = None
        self._session_name: str = ""
        self._definitions: list[TestDefinition] = []
        self._runs: list[TestRun] = []
        self._current_run: TestRun | None = None
        self._current_results: list[TestCaseResult] = []
        self._results_by_func: dict[str, TestCaseResult] = {}
        self._worker: TestExecutionWorker | None = None
        self._active_run_session_id: str | None = None
        self._active_run_id: str | None = None
        self._extra_env: dict[str, str] = {}

        self._build_ui()
        self._update_controls()

    @property
    def is_running(self) -> bool:
        """Return True while a background test execution worker is active."""
        return self._worker is not None

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        self.session_label = QLabel("No session selected.", self)
        self.session_label.setObjectName("mutedLabel")

        self.headed_checkbox = QCheckBox("Headed", self)
        self.headed_checkbox.setChecked(False)

        self.env_vars_btn = QPushButton("Env / Secrets", self)
        self.env_vars_btn.clicked.connect(self._on_edit_env_vars)

        self.run_selected_btn = QPushButton("Run Selected", self)
        self.run_selected_btn.setObjectName("primaryButton")
        self.run_selected_btn.clicked.connect(self._on_run_selected)

        self.run_scenario_btn = QPushButton("Run Scenario", self)
        self.run_scenario_btn.clicked.connect(self._on_run_scenario)

        self.run_independent_btn = QPushButton("Run Independent", self)
        self.run_independent_btn.clicked.connect(self._on_run_independent)

        self.run_journey_btn = QPushButton("Run Journey", self)
        self.run_journey_btn.clicked.connect(self._on_run_journey)

        self.run_suite_btn = QPushButton("Run Suite", self)
        self.run_suite_btn.clicked.connect(self._on_run_complete_suite)

        self.rerun_failed_btn = QPushButton("Rerun Failed", self)
        self.rerun_failed_btn.clicked.connect(self._on_rerun_failed)

        self.stop_btn = QPushButton("Stop", self)
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.clicked.connect(self._on_stop)

        toolbar.addWidget(self.session_label, stretch=1)
        toolbar.addWidget(self.headed_checkbox)
        toolbar.addWidget(self.env_vars_btn)
        toolbar.addWidget(self.run_selected_btn)
        toolbar.addWidget(self.run_scenario_btn)
        toolbar.addWidget(self.run_independent_btn)
        toolbar.addWidget(self.run_journey_btn)
        toolbar.addWidget(self.run_suite_btn)
        toolbar.addWidget(self.rerun_failed_btn)
        toolbar.addWidget(self.stop_btn)
        root.addLayout(toolbar)

        self.summary_label = QLabel("Idle — select tests and click Run.", self)
        self.summary_label.setObjectName("mutedLabel")
        root.addWidget(self.summary_label)

        # Main splitter: Left (Tests + History) | Right (Results + Diagnostics)
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left_splitter = QSplitter(Qt.Orientation.Vertical, self)

        # Left-Top: Test Definitions checklist
        tests_panel = QWidget(self)
        tests_layout = QVBoxLayout(tests_panel)
        tests_layout.setContentsMargins(0, 0, 0, 0)
        tests_layout.setSpacing(4)

        tests_header = QHBoxLayout()
        tests_title = QLabel("Tests in Session", tests_panel)
        tests_title.setObjectName("sectionTitle")
        self.refresh_btn = QPushButton("Refresh", tests_panel)
        self.refresh_btn.clicked.connect(self.refresh)
        tests_header.addWidget(tests_title, stretch=1)
        tests_header.addWidget(self.refresh_btn)

        self.tests_list = QListWidget(tests_panel)
        tests_layout.addLayout(tests_header)
        tests_layout.addWidget(self.tests_list, stretch=1)

        # Left-Bottom: Execution History
        history_panel = QWidget(self)
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(4)

        history_title = QLabel("Execution History", history_panel)
        history_title.setObjectName("sectionTitle")
        self.history_list = QListWidget(history_panel)
        self.history_list.currentRowChanged.connect(self._on_history_selected)
        history_layout.addWidget(history_title)
        history_layout.addWidget(self.history_list, stretch=1)

        left_splitter.addWidget(tests_panel)
        left_splitter.addWidget(history_panel)
        left_splitter.setSizes([240, 220])

        # Right splitter: Results table (top) | Diagnostics tabs (bottom)
        right_splitter = QSplitter(Qt.Orientation.Vertical, self)

        results_panel = QWidget(self)
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(4)

        results_title = QLabel("Test Results", results_panel)
        results_title.setObjectName("sectionTitle")
        self.results_table = QTableWidget(0, 4, results_panel)
        self.results_table.setHorizontalHeaderLabels(["Status", "Test", "Duration", "Error"])
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.itemSelectionChanged.connect(self._on_result_selected)
        results_layout.addWidget(results_title)
        results_layout.addWidget(self.results_table, stretch=1)

        # Diagnostics tabs
        self.detail_tabs = QTabWidget(self)

        self.traceback_view = QPlainTextEdit(self)
        self.traceback_view.setReadOnly(True)
        self.traceback_view.setObjectName("codePreview")
        self.detail_tabs.addTab(self.traceback_view, "Traceback & Output")

        self.errors_view = QPlainTextEdit(self)
        self.errors_view.setReadOnly(True)
        self.errors_view.setObjectName("codePreview")
        self.detail_tabs.addTab(self.errors_view, "Console & Network")

        self.screenshot_label = QLabel("No failure screenshot.", self)
        self.screenshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screenshot_label.setObjectName("mutedLabel")
        self.detail_tabs.addTab(self.screenshot_label, "Failure Screenshot")

        self.live_log_view = QPlainTextEdit(self)
        self.live_log_view.setReadOnly(True)
        self.live_log_view.setObjectName("codePreview")
        self.detail_tabs.addTab(self.live_log_view, "Live Run Log")

        right_splitter.addWidget(results_panel)
        right_splitter.addWidget(self.detail_tabs)
        right_splitter.setSizes([240, 260])

        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([300, 620])
        root.addWidget(main_splitter, stretch=1)

    def load_session(self, session_id: str | None, session_name: str = "") -> None:
        """Bind this tab to a recording session and load its definitions and run history."""
        if self.is_running and session_id != self._active_run_session_id:
            return
        self._session_id = session_id
        self._session_name = session_name
        self._current_run = None
        self._current_results = []
        self._results_by_func = {}
        self.results_table.setRowCount(0)
        self._clear_details()
        self.live_log_view.clear()

        if session_id is None:
            self.session_label.setText("No session selected.")
            self.summary_label.setText("Idle — select a recording session first.")
            self.tests_list.clear()
            self.history_list.clear()
            self._definitions = []
            self._runs = []
            self._update_controls()
            return

        label = session_name or session_id[:8]
        self.session_label.setText(f"Session: {label}")
        self.refresh()

    def refresh(self) -> None:
        """Reload test definitions and execution history for the active session."""
        if self._session_id is None:
            return
        self._definitions = self._definition_service.list_by_session(self._session_id)
        self._populate_tests_list()
        self._runs = self._execution_service.list_runs_by_session(self._session_id)
        self._populate_history_list()
        self._update_controls()

    def shutdown(self) -> None:
        """Cancel and wait for any active execution worker before window exit."""
        worker = self._worker
        if worker is not None:
            worker.request_cancel()
            if not worker.wait(5000):
                worker.wait(5000)
            if not worker.isRunning():
                worker.deleteLater()
                self._worker = None
                self._active_run_session_id = None
                self._active_run_id = None

    def _populate_tests_list(self) -> None:
        self.tests_list.clear()
        for defn in self._definitions:
            tag = "journey" if defn.mode.value == "journey" else "indep"
            item = QListWidgetItem(f"[{tag}] {defn.name}")
            item.setData(Qt.ItemDataRole.UserRole, defn.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if defn.included else Qt.CheckState.Unchecked)
            self.tests_list.addItem(item)

    def _populate_history_list(self, select_run_id: str | None = None) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        selected_row = -1
        for idx, run in enumerate(self._runs):
            ts = run.started_at.strftime("%H:%M:%S")
            text = (
                f"[{run.status.value.upper()}] {run.mode.value} — "
                f"{run.passed_count}/{run.total_count} passed ({ts})"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, run.id)
            self.history_list.addItem(item)
            if select_run_id and run.id == select_run_id:
                selected_row = idx
        self.history_list.blockSignals(False)
        if selected_row >= 0:
            self.history_list.setCurrentRow(selected_row)

    def _selected_definition_ids(self) -> list[str]:
        ids: list[str] = []
        for idx in range(self.tests_list.count()):
            item = self.tests_list.item(idx)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _start_worker(
        self,
        *,
        definition_ids: list[str] | None = None,
        scenario_id: str | None = None,
        complete_suite: bool = False,
        mode: ExecutionMode = ExecutionMode.INDEPENDENT,
        rerun_failed_run_id: str | None = None,
    ) -> None:
        if self._session_id is None or self._worker is not None:
            return
        self._current_run = None
        self._current_results = []
        self._results_by_func = {}
        self.results_table.setRowCount(0)
        self._clear_details()
        self.live_log_view.clear()
        self.summary_label.setText(f"Starting {mode.value} execution...")

        worker = TestExecutionWorker(
            self._execution_service,
            session_id=self._session_id,
            definition_ids=definition_ids,
            scenario_id=scenario_id,
            complete_suite=complete_suite,
            mode=mode,
            rerun_failed_run_id=rerun_failed_run_id,
            headed=self.headed_checkbox.isChecked(),
            extra_env=self._extra_env or None,
            parent=self,
        )
        worker.progress_event.connect(self._on_progress_event)
        worker.run_completed.connect(self._on_run_completed)
        worker.run_failed.connect(self._on_run_failed)
        self._worker = worker
        self._active_run_session_id = self._session_id
        self._active_run_id = None
        self._update_controls()
        self.execution_started.emit()
        worker.start()

    def _on_edit_env_vars(self) -> None:
        existing_text = "\n".join(f"{k}={v}" for k, v in self._extra_env.items())
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Execution Environment Variables / Test Secrets",
            "Enter KEY=VALUE pairs (one per line, e.g. TEST_SECRET_STEP_2=my-secret):",
            existing_text,
        )
        if not ok:
            return
        parsed: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            key = key.strip()
            if key:
                parsed[key] = val.strip()
        self._extra_env = parsed
        count = len(parsed)
        self.env_vars_btn.setText(f"Env / Secrets ({count})" if count else "Env / Secrets")

    def _on_run_selected(self) -> None:
        ids = self._selected_definition_ids()
        if not ids:
            QMessageBox.information(
                self,
                "No Tests Selected",
                "Check at least one test in 'Tests in Session' to run.",
            )
            return
        by_id = {d.id: d for d in self._definitions}
        selected_defs = [by_id[i] for i in ids if i in by_id]
        mode = (
            ExecutionMode.JOURNEY
            if selected_defs and all(d.mode == TestMode.JOURNEY for d in selected_defs)
            else ExecutionMode.INDEPENDENT
        )
        self._start_worker(definition_ids=ids, mode=mode)

    def _on_run_scenario(self) -> None:
        current_item = self.tests_list.currentItem()
        if current_item is None:
            QMessageBox.information(
                self,
                "No Scenario Selected",
                "Highlight a scenario test in 'Tests in Session' first.",
            )
            return
        def_id = str(current_item.data(Qt.ItemDataRole.UserRole))
        defn = next((d for d in self._definitions if d.id == def_id), None)
        if defn is None or defn.scenario_id is None:
            QMessageBox.information(
                self,
                "Not a Scenario Test",
                "Highlight an independent scenario test to run its scenario.",
            )
            return
        self._start_worker(scenario_id=defn.scenario_id, mode=ExecutionMode.INDEPENDENT)

    def _on_run_independent(self) -> None:
        self._start_worker(mode=ExecutionMode.INDEPENDENT)

    def _on_run_journey(self) -> None:
        self._start_worker(mode=ExecutionMode.JOURNEY)

    def _on_run_complete_suite(self) -> None:
        self._start_worker(complete_suite=True, mode=ExecutionMode.INDEPENDENT)

    def _on_rerun_failed(self) -> None:
        if self._current_run is None:
            return
        self._start_worker(
            mode=self._current_run.mode,
            rerun_failed_run_id=self._current_run.id,
        )

    def _on_stop(self) -> None:
        if self._worker is not None:
            self.summary_label.setText("Stopping test execution...")
            self.stop_btn.setEnabled(False)
            self._worker.request_cancel()

    def _on_progress_event(self, event_obj: object) -> None:
        if self._worker is None or not isinstance(event_obj, ExecutionProgressEvent):
            return
        if event_obj.event_type == "run_started":
            self._active_run_id = event_obj.run_id
            self.summary_label.setText(event_obj.message)
            return
        if self._active_run_id is not None and event_obj.run_id != self._active_run_id:
            return
        if event_obj.event_type == "log":
            self.live_log_view.appendPlainText(event_obj.message)
            return
        if event_obj.event_type in ("test_started", "test_finished") and event_obj.result:
            self._upsert_result_row(event_obj.result)
            return
        if event_obj.event_type == "run_finished":
            self.summary_label.setText(event_obj.message)

    def _on_run_completed(self, run_obj: object, results_obj: object) -> None:
        self._cleanup_worker()
        if (
            isinstance(run_obj, TestRun)
            and isinstance(results_obj, list)
            and run_obj.session_id == self._session_id
        ):
            self._current_run = run_obj
            self._current_results = [r for r in results_obj if isinstance(r, TestCaseResult)]
            self._render_results_table(self._current_results)
            self.summary_label.setText(
                f"Run {run_obj.status.value.upper()}: "
                f"{run_obj.passed_count} passed | {run_obj.failed_count} failed | "
                f"{run_obj.skipped_count} skipped ({run_obj.duration_ms / 1000:.2f}s)"
            )
            if self._session_id is not None:
                self._runs = self._execution_service.list_runs_by_session(self._session_id)
                self._populate_history_list(select_run_id=run_obj.id)
        self._update_controls()

    def _on_run_failed(self, error_message: str) -> None:
        self._cleanup_worker()
        self.summary_label.setText(f"Execution error: {error_message}")
        QMessageBox.warning(self, "Execution Error", error_message)
        self._update_controls()

    def _cleanup_worker(self) -> None:
        worker = self._worker
        self._worker = None
        self._active_run_session_id = None
        self._active_run_id = None
        if worker is not None:
            worker.wait(3000)
            worker.deleteLater()
            self.execution_finished.emit()

    def _on_history_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._runs) or self._worker is not None:
            return
        selected_run = self._runs[row]
        try:
            run, results = self._execution_service.get_run_with_results(selected_run.id)
        except ExecutionError as exc:
            QMessageBox.warning(self, "Could Not Load Run", str(exc))
            return

        self._current_run = run
        self._current_results = results
        self._render_results_table(results)
        self.summary_label.setText(
            f"Historical Run [{run.status.value.upper()} / {run.mode.value}]: "
            f"{run.passed_count} passed | {run.failed_count} failed | "
            f"{run.skipped_count} skipped ({run.duration_ms / 1000:.2f}s)"
        )
        log_path = self._execution_service.get_artifact_path(run.log_artifact_id)
        if log_path is not None:
            try:
                self.live_log_view.setPlainText(
                    log_path.read_text(encoding="utf-8", errors="replace")
                )
            except OSError as exc:
                self.live_log_view.setPlainText(f"(Could not read execution log: {exc})")
        else:
            self.live_log_view.setPlainText("(No execution log available.)")
        self._update_controls()

    def _render_results_table(self, results: list[TestCaseResult]) -> None:
        self._results_by_func = {}
        self.results_table.setRowCount(0)
        for res in results:
            self._upsert_result_row(res)
        if results:
            self.results_table.selectRow(0)
        else:
            self._clear_details()

    def _upsert_result_row(self, result: TestCaseResult) -> None:
        self._results_by_func[result.function_name] = result
        target_row = -1
        for row_idx in range(self.results_table.rowCount()):
            item = self.results_table.item(row_idx, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == result.function_name:
                target_row = row_idx
                break

        if target_row == -1:
            target_row = self.results_table.rowCount()
            self.results_table.insertRow(target_row)

        name_item = QTableWidgetItem(result.test_name or result.function_name)
        name_item.setData(Qt.ItemDataRole.UserRole, result.function_name)
        dur_str = f"{result.duration_ms} ms" if result.duration_ms else "-"

        self.results_table.setItem(target_row, 0, QTableWidgetItem(result.status.value.upper()))
        self.results_table.setItem(target_row, 1, name_item)
        self.results_table.setItem(target_row, 2, QTableWidgetItem(dur_str))
        self.results_table.setItem(target_row, 3, QTableWidgetItem(result.error_message))

    def _on_result_selected(self) -> None:
        row = self.results_table.currentRow()
        if row < 0:
            self._clear_details()
            return
        item = self.results_table.item(row, 1)
        if item is None:
            self._clear_details()
            return
        func_name = str(item.data(Qt.ItemDataRole.UserRole))
        result = self._results_by_func.get(func_name)
        if result is None:
            self._clear_details()
            return

        tb_parts: list[str] = []
        if result.error_message:
            tb_parts.append(f"Error: {result.error_message}")
        if result.traceback:
            tb_parts.append(result.traceback)
        matched_def = next(
            (
                d
                for d in self._definitions
                if (result.definition_id and d.id == result.definition_id)
                or d.function_name == result.function_name
            ),
            None,
        )
        if matched_def is not None and matched_def.actions:
            header = (
                f"=== Step Breakdown ({matched_def.mode.value}: "
                f"{len(matched_def.actions)} step(s)) ==="
            )
            step_lines = [header]
            for action in matched_def.actions:
                step_lines.append(
                    f"  {action.order_index}. [{action.action_type.value}] {action.summary}"
                )
            tb_parts.append("\n".join(step_lines))
        if result.stdout:
            tb_parts.append(f"--- STDOUT ---\n{result.stdout}")
        if result.stderr:
            tb_parts.append(f"--- STDERR ---\n{result.stderr}")
        self.traceback_view.setPlainText(
            "\n\n".join(tb_parts) if tb_parts else "No failure traceback or captured output."
        )

        err_parts: list[str] = []
        if result.console_errors:
            err_parts.append("=== Console Errors ===\n" + "\n".join(result.console_errors))
        if result.network_errors:
            err_parts.append("=== Network Errors ===\n" + "\n".join(result.network_errors))
        self.errors_view.setPlainText(
            "\n\n".join(err_parts)
            if err_parts
            else "No browser console or network errors captured."
        )

        screenshot_path = self._execution_service.get_artifact_path(result.screenshot_artifact_id)
        if screenshot_path is not None:
            pixmap = QPixmap(str(screenshot_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    640,
                    360,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.screenshot_label.setPixmap(scaled)
            else:
                self.screenshot_label.setPixmap(QPixmap())
                self.screenshot_label.setText("Failed to render screenshot.")
        else:
            self.screenshot_label.setPixmap(QPixmap())
            self.screenshot_label.setText("No failure screenshot.")

    def _clear_details(self) -> None:
        self.traceback_view.clear()
        self.errors_view.clear()
        self.screenshot_label.setPixmap(QPixmap())
        self.screenshot_label.setText("No failure screenshot.")

    def _update_controls(self) -> None:
        running = self._worker is not None
        has_session = self._session_id is not None
        has_defs = bool(self._definitions)
        can_rerun = (
            not running and self._current_run is not None and self._current_run.failed_count > 0
        )

        self.run_selected_btn.setEnabled(has_session and has_defs and not running)
        self.run_scenario_btn.setEnabled(
            has_session
            and any(d.scenario_id is not None for d in self._definitions)
            and not running
        )
        self.run_independent_btn.setEnabled(has_session and has_defs and not running)
        self.run_journey_btn.setEnabled(has_session and has_defs and not running)
        self.run_suite_btn.setEnabled(has_session and has_defs and not running)
        self.rerun_failed_btn.setEnabled(can_rerun)
        self.stop_btn.setEnabled(running)
        self.refresh_btn.setEnabled(has_session and not running)
        self.headed_checkbox.setEnabled(not running)
        self.env_vars_btn.setEnabled(not running)
