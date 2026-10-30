"""Timeline and Scenario Review widget for Phase 3."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QVBoxLayout,
    QWidget,
)

from src.processing import ProcessedEvent, ProcessingResult
from src.scenarios import Scenario, ScenarioError, ScenarioService
from src.ui.workers import SessionProcessingWorker


class ReviewWidget(QWidget):
    """Interactive review panel for correlated timelines and editable scenarios."""

    scenarios_changed = Signal(str)

    def __init__(
        self,
        scenario_service: ScenarioService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scenario_service = scenario_service
        self._session_id: str | None = None
        self._session_name: str = ""
        self._scenarios: list[Scenario] = []
        self._timeline: list[ProcessedEvent] = []
        self._visible_events: list[ProcessedEvent] = []
        self._worker: SessionProcessingWorker | None = None

        self._build_ui()
        self._update_controls()

    @property
    def is_processing(self) -> bool:
        """Return True while background session processing is running."""
        return self._worker is not None

    def shutdown(self) -> None:
        """Wait for any active background processing worker before widget teardown."""
        worker = self._worker
        if worker is not None:
            worker.wait(5000)
            if not worker.isRunning():
                worker.deleteLater()
                self._worker = None

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Top toolbar
        toolbar = QHBoxLayout()
        self.summary_label = QLabel("Select a recording session to process and review.", self)
        self.summary_label.setObjectName("mutedLabel")

        self.show_all_steps_btn = QPushButton("Show All Steps", self)
        self.show_all_steps_btn.setCheckable(True)
        self.show_all_steps_btn.toggled.connect(self._refresh_timeline_table)

        self.process_btn = QPushButton("Process Session", self)
        self.process_btn.setObjectName("primaryButton")
        self.process_btn.clicked.connect(self.process_current_session)

        toolbar.addWidget(self.summary_label, stretch=1)
        toolbar.addWidget(self.show_all_steps_btn)
        toolbar.addWidget(self.process_btn)

        # Main horizontal splitter: Scenarios (left) | Timeline + Evidence Inspector (right)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left: Scenarios pane
        scenarios_pane = QWidget(splitter)
        scenarios_layout = QVBoxLayout(scenarios_pane)
        scenarios_layout.setContentsMargins(0, 0, 6, 0)
        scenarios_layout.setSpacing(6)

        scenarios_title = QLabel("Detected Scenarios", scenarios_pane)
        scenarios_title.setObjectName("sectionTitle")

        self.scenarios_list = QListWidget(scenarios_pane)
        self.scenarios_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.scenarios_list.itemSelectionChanged.connect(self._on_scenario_selection_changed)

        row1 = QHBoxLayout()
        self.rename_btn = QPushButton("Rename", scenarios_pane)
        self.rename_btn.clicked.connect(self._on_rename_scenario)
        self.toggle_include_btn = QPushButton("Exclude", scenarios_pane)
        self.toggle_include_btn.clicked.connect(self._on_toggle_include)
        self.delete_btn = QPushButton("Delete", scenarios_pane)
        self.delete_btn.clicked.connect(self._on_delete_scenario)
        row1.addWidget(self.rename_btn)
        row1.addWidget(self.toggle_include_btn)
        row1.addWidget(self.delete_btn)

        row2 = QHBoxLayout()
        self.merge_btn = QPushButton("Merge", scenarios_pane)
        self.merge_btn.clicked.connect(self._on_merge_scenarios)
        self.split_btn = QPushButton("Split Here", scenarios_pane)
        self.split_btn.clicked.connect(self._on_split_scenario)
        self.move_up_btn = QPushButton("Up", scenarios_pane)
        self.move_up_btn.clicked.connect(lambda: self._on_move_scenario(-1))
        self.move_down_btn = QPushButton("Down", scenarios_pane)
        self.move_down_btn.clicked.connect(lambda: self._on_move_scenario(1))
        row2.addWidget(self.merge_btn)
        row2.addWidget(self.split_btn)
        row2.addWidget(self.move_up_btn)
        row2.addWidget(self.move_down_btn)

        scenarios_layout.addWidget(scenarios_title)
        scenarios_layout.addWidget(self.scenarios_list, stretch=1)
        scenarios_layout.addLayout(row1)
        scenarios_layout.addLayout(row2)

        # Right: Timeline table + Evidence Inspector
        right_splitter = QSplitter(Qt.Orientation.Vertical, splitter)

        timeline_pane = QWidget(right_splitter)
        timeline_layout = QVBoxLayout(timeline_pane)
        timeline_layout.setContentsMargins(6, 0, 0, 4)
        timeline_layout.setSpacing(6)

        timeline_title = QLabel("Correlated Timeline", timeline_pane)
        timeline_title.setObjectName("sectionTitle")

        self.timeline_table = QTableWidget(0, 5, timeline_pane)
        self.timeline_table.setHorizontalHeaderLabels(
            ["#", "Action", "Summary", "Correlated Outcomes", "Evidence"]
        )
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.itemSelectionChanged.connect(self._on_timeline_selection_changed)

        timeline_layout.addWidget(timeline_title)
        timeline_layout.addWidget(self.timeline_table, stretch=1)

        inspector_pane = QWidget(right_splitter)
        inspector_layout = QVBoxLayout(inspector_pane)
        inspector_layout.setContentsMargins(6, 4, 0, 0)
        inspector_layout.setSpacing(4)

        inspector_title = QLabel("Step Evidence & Traceability", inspector_pane)
        inspector_title.setObjectName("mutedLabel")
        self.evidence_inspector = QPlainTextEdit(inspector_pane)
        self.evidence_inspector.setReadOnly(True)
        self.evidence_inspector.setPlaceholderText(
            "Select a timeline step to inspect target metadata, "
            "correlated outcomes, and raw event IDs."
        )
        inspector_layout.addWidget(inspector_title)
        inspector_layout.addWidget(self.evidence_inspector, stretch=1)

        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)

        splitter.addWidget(scenarios_pane)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 620])

        layout.addLayout(toolbar)
        layout.addWidget(splitter, stretch=1)

    def load_session(self, session_id: str | None, session_name: str = "") -> None:
        """Load persisted processed events and scenarios for the given session."""
        self._session_id = session_id
        self._session_name = session_name
        if session_id is None:
            self._scenarios = []
            self._timeline = []
            self._visible_events = []
            self.scenarios_list.clear()
            self.timeline_table.setRowCount(0)
            self.evidence_inspector.clear()
            self.summary_label.setText("Select a recording session to process and review.")
            self._update_controls()
            return

        self._reload_from_storage()

    def process_current_session(self) -> None:
        """Run deterministic processing and scenario detection off the UI thread."""
        if self._session_id is None or self._worker is not None:
            return
        worker = SessionProcessingWorker(self._scenario_service, self._session_id, parent=self)
        worker.processing_finished.connect(self._on_processing_finished)
        worker.processing_failed.connect(self._on_processing_failed)
        self._worker = worker
        label = self._session_name or self._session_id[:8]
        self.summary_label.setText(f"{label}: Processing raw events...")
        self._update_controls()
        worker.start()

    def _on_processing_finished(
        self,
        session_id: str,
        result_obj: object,
        scenarios_obj: object,
    ) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()

        if session_id != self._session_id:
            self._update_controls()
            return
        if not isinstance(result_obj, ProcessingResult) or not isinstance(scenarios_obj, list):
            self._update_controls()
            return

        scenarios: list[Scenario] = scenarios_obj
        self._timeline = result_obj.processed_events
        self._scenarios = scenarios
        self._render_scenarios(select_index=0 if scenarios else -1)
        self._refresh_timeline_table()
        self.summary_label.setText(
            f"{self._session_name or self._session_id[:8]}: "
            f"{result_obj.raw_event_count} raw -> {len(result_obj.processed_events)} steps -> "
            f"{len(scenarios)} scenario(s) ({result_obj.dropped_count} filtered)"
        )
        self._update_controls()
        self.scenarios_changed.emit(self._session_id)

    def _on_processing_failed(self, session_id: str, error_message: str) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()

        self._update_controls()
        if session_id == self._session_id:
            QMessageBox.warning(self, "Processing Failed", error_message)

    def _reload_from_storage(self, select_scenario_id: str | None = None) -> None:
        if self._session_id is None:
            return
        self._timeline = self._scenario_service.get_timeline(self._session_id)
        self._scenarios = self._scenario_service.list_scenarios(self._session_id)

        select_idx = 0 if self._scenarios else -1
        if select_scenario_id is not None:
            for idx, sc in enumerate(self._scenarios):
                if sc.id == select_scenario_id:
                    select_idx = idx
                    break

        self._render_scenarios(select_index=select_idx)
        self._refresh_timeline_table()
        label = self._session_name or self._session_id[:8]
        if self._timeline or self._scenarios:
            self.summary_label.setText(
                f"{label}: {len(self._timeline)} processed step(s), "
                f"{len(self._scenarios)} scenario(s)"
            )
        else:
            self.summary_label.setText(
                f"{label}: Not processed yet. Click 'Process Session' to analyze raw events."
            )
        self._update_controls()

    def _render_scenarios(self, select_index: int = -1) -> None:
        self.scenarios_list.blockSignals(True)
        self.scenarios_list.clear()
        for scenario in self._scenarios:
            prefix = "[Included]" if scenario.included else "[Excluded]"
            step_count = len(scenario.processed_event_ids)
            text = f"{scenario.order_index}. {prefix} {scenario.name} ({step_count})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, scenario.id)
            self.scenarios_list.addItem(item)

        if 0 <= select_index < self.scenarios_list.count():
            self.scenarios_list.setCurrentRow(select_index)
        self.scenarios_list.blockSignals(False)

    def _selected_scenarios(self) -> list[Scenario]:
        selected_ids = {
            str(item.data(Qt.ItemDataRole.UserRole)) for item in self.scenarios_list.selectedItems()
        }
        return [s for s in self._scenarios if s.id in selected_ids]

    def _on_scenario_selection_changed(self) -> None:
        self._refresh_timeline_table()
        self._update_controls()

    def _refresh_timeline_table(self) -> None:
        if self.show_all_steps_btn.isChecked() or not self._scenarios:
            events = list(self._timeline)
        else:
            selected = self._selected_scenarios()
            if len(selected) == 1:
                by_id = {e.id: e for e in self._timeline}
                events = [by_id[eid] for eid in selected[0].processed_event_ids if eid in by_id]
            elif len(selected) > 1:
                by_id = {e.id: e for e in self._timeline}
                events = [
                    by_id[eid] for sc in selected for eid in sc.processed_event_ids if eid in by_id
                ]
            else:
                events = list(self._timeline)

        self._visible_events = events
        self.timeline_table.setRowCount(len(events))
        for row_idx, event in enumerate(events):
            outcomes_text = "; ".join(o.summary for o in event.outcomes) if event.outcomes else "-"
            evidence_text = f"{len(event.raw_event_ids)} raw"
            self.timeline_table.setItem(row_idx, 0, QTableWidgetItem(str(event.order_index)))
            self.timeline_table.setItem(row_idx, 1, QTableWidgetItem(event.action_type.value))
            self.timeline_table.setItem(row_idx, 2, QTableWidgetItem(event.summary))
            self.timeline_table.setItem(row_idx, 3, QTableWidgetItem(outcomes_text))
            self.timeline_table.setItem(row_idx, 4, QTableWidgetItem(evidence_text))

        self.evidence_inspector.clear()
        self._update_controls()

    def _on_timeline_selection_changed(self) -> None:
        row = self.timeline_table.currentRow()
        if 0 <= row < len(self._visible_events):
            event = self._visible_events[row]
            details = {
                "step_id": event.id,
                "action_type": event.action_type.value,
                "summary": event.summary,
                "page_id": event.page_id,
                "target": event.target,
                "payload": event.payload,
                "outcomes": [
                    {
                        "type": o.outcome_type.value,
                        "summary": o.summary,
                        "details": o.details,
                        "raw_event_ids": o.raw_event_ids,
                    }
                    for o in event.outcomes
                ],
                "raw_event_ids": event.raw_event_ids,
            }
            self.evidence_inspector.setPlainText(json.dumps(details, indent=2, ensure_ascii=False))
        else:
            self.evidence_inspector.clear()
        self._update_controls()

    def _on_rename_scenario(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) != 1:
            return
        scenario = selected[0]
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Scenario",
            "Scenario name:",
            text=scenario.name,
        )
        if not ok:
            return
        try:
            updated = self._scenario_service.rename_scenario(scenario.id, new_name)
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Rename Scenario", str(exc))
            return
        self._reload_from_storage(select_scenario_id=updated.id)

    def _on_toggle_include(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) != 1:
            return
        scenario = selected[0]
        try:
            updated = self._scenario_service.set_scenario_included(
                scenario.id, not scenario.included
            )
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Update Scenario", str(exc))
            return
        self._reload_from_storage(select_scenario_id=updated.id)

    def _on_delete_scenario(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) != 1:
            return
        scenario = selected[0]
        try:
            self._scenario_service.delete_scenario(scenario.id)
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Delete Scenario", str(exc))
            return
        self._reload_from_storage()

    def _on_merge_scenarios(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) < 2:
            return
        try:
            merged = self._scenario_service.merge_scenarios([s.id for s in selected])
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Merge Scenarios", str(exc))
            return
        self._reload_from_storage(select_scenario_id=merged.id)

    def _on_split_scenario(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) != 1 or self.show_all_steps_btn.isChecked():
            return
        scenario = selected[0]
        split_row = self.timeline_table.currentRow()
        if split_row < 1 or split_row >= len(scenario.processed_event_ids):
            QMessageBox.information(
                self,
                "Select Split Point",
                "Select a step (from step 2 onward) in the timeline table to split this scenario.",
            )
            return
        try:
            left, _right = self._scenario_service.split_scenario(scenario.id, split_row)
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Split Scenario", str(exc))
            return
        self._reload_from_storage(select_scenario_id=left.id)

    def _on_move_scenario(self, delta: int) -> None:
        if self._session_id is None:
            return
        selected = self._selected_scenarios()
        if len(selected) != 1:
            return
        scenario = selected[0]
        ids = [s.id for s in self._scenarios]
        idx = ids.index(scenario.id)
        target_idx = idx + delta
        if not (0 <= target_idx < len(ids)):
            return
        ids[idx], ids[target_idx] = ids[target_idx], ids[idx]
        try:
            self._scenario_service.reorder_scenarios(self._session_id, ids)
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Reorder Scenarios", str(exc))
            return
        self._reload_from_storage(select_scenario_id=scenario.id)

    def _update_controls(self) -> None:
        has_session = self._session_id is not None
        busy = self._worker is not None
        selected = self._selected_scenarios()
        single = len(selected) == 1
        multi = len(selected) >= 2

        self.process_btn.setEnabled(has_session and not busy)
        self.show_all_steps_btn.setEnabled(has_session and not busy and bool(self._timeline))
        self.rename_btn.setEnabled(single and not busy)
        self.toggle_include_btn.setEnabled(single and not busy)
        self.delete_btn.setEnabled(single and not busy)
        self.merge_btn.setEnabled(multi and not busy)

        if single and not busy:
            sc = selected[0]
            self.toggle_include_btn.setText("Exclude" if sc.included else "Include")
            idx = self._scenarios.index(sc)
            self.move_up_btn.setEnabled(idx > 0)
            self.move_down_btn.setEnabled(idx < len(self._scenarios) - 1)
            can_split = (
                not self.show_all_steps_btn.isChecked()
                and len(sc.processed_event_ids) >= 2
                and self.timeline_table.currentRow() >= 1
            )
            self.split_btn.setEnabled(can_split)
        else:
            self.toggle_include_btn.setText("Exclude")
            self.move_up_btn.setEnabled(False)
            self.move_down_btn.setEnabled(False)
            self.split_btn.setEnabled(False)
