"""Timeline and Scenario Review widget for Phase 3."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.browser.reproducer import ReproductionStepResult, ReproductionSummary
from src.export import RecordingBundleError, RecordingBundleService, export_recording_pdf
from src.processing import CorrelatedOutcome, ProcessedEvent, ProcessingResult
from src.projects import SessionService
from src.scenarios import Scenario, ScenarioError, ScenarioService
from src.storage import ArtifactManager, ArtifactRecord, ArtifactRepository
from src.ui.theme import (
    TOKENS,
    make_icon,
    make_vseparator,
    set_button_state,
)
from src.ui.workers import RecordingReproductionWorker, SessionProcessingWorker


class ReviewWidget(QWidget):
    """Interactive review panel for correlated timelines and editable scenarios."""

    scenarios_changed = Signal(str)
    processing_started = Signal()
    processing_finished = Signal()

    def __init__(
        self,
        scenario_service: ScenarioService,
        parent: QWidget | None = None,
        *,
        artifacts: ArtifactManager | None = None,
        artifact_repository: ArtifactRepository | None = None,
        recording_bundle_service: RecordingBundleService | None = None,
        session_service: SessionService | None = None,
    ) -> None:
        super().__init__(parent)
        self._scenario_service = scenario_service
        self._artifacts = artifacts
        self._artifact_repo = artifact_repository
        self._bundle_service = recording_bundle_service
        self._session_service = session_service
        self._session_id: str | None = None
        self._session_name: str = ""
        self._scenarios: list[Scenario] = []
        self._timeline: list[ProcessedEvent] = []
        self._visible_events: list[ProcessedEvent] = []
        self._uncorrelated_outcomes: list[CorrelatedOutcome] = []
        self._video_artifacts: list[ArtifactRecord] = []
        self._worker: SessionProcessingWorker | None = None
        self._repro_worker: RecordingReproductionWorker | None = None
        self._external_busy: bool = False

        self._build_ui()
        self._update_controls()

    @property
    def is_processing(self) -> bool:
        """Return True while background session processing or reproduction is running."""
        return self._worker is not None or self._repro_worker is not None

    def set_external_busy(self, busy: bool) -> None:
        """Lock/unlock review controls while another tab holds an active worker."""
        if self._external_busy != busy:
            self._external_busy = busy
            self._update_controls()

    def shutdown(self) -> None:
        """Disconnect and wait for any active background worker before teardown."""
        worker = self._worker
        if worker is not None:
            for sig in (worker.processing_finished, worker.processing_failed):
                with suppress(RuntimeError, TypeError):
                    sig.disconnect()
            worker.wait(5000)
            if not worker.isRunning():
                worker.deleteLater()
            else:
                worker.setParent(None)
            self._worker = None

        repro = self._repro_worker
        if repro is not None:
            for sig in (
                repro.step_completed,
                repro.reproduction_finished,
                repro.reproduction_failed,
            ):
                with suppress(RuntimeError, TypeError):
                    sig.disconnect()
            repro.request_cancel()
            repro.wait(5000)
            if not repro.isRunning():
                repro.deleteLater()
            else:
                repro.setParent(None)
            self._repro_worker = None

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Tier 1: Primary header strip (Summary + Reproduce + Process)
        primary_strip = QFrame(self)
        primary_strip.setObjectName("toolbarStrip")
        primary_row = QHBoxLayout(primary_strip)
        primary_row.setContentsMargins(12, 8, 12, 8)
        primary_row.setSpacing(8)

        self.summary_label = QLabel(
            "Select a recording session to process and review.", primary_strip
        )
        self.summary_label.setObjectName("mutedLabel")

        self.reproduce_btn = QPushButton("Reproduce", primary_strip)
        self.reproduce_btn.setIcon(make_icon("replay", TOKENS.text_primary, 14))
        self.reproduce_btn.clicked.connect(self._on_toggle_reproduce)

        self.process_btn = QPushButton("Process Session", primary_strip)
        self.process_btn.setObjectName("primaryButton")
        self.process_btn.setIcon(make_icon("spark", TOKENS.accent_fg, 14))
        self.process_btn.clicked.connect(self.process_current_session)

        primary_row.addWidget(self.summary_label, stretch=1)
        primary_row.addWidget(self.reproduce_btn)
        primary_row.addWidget(self.process_btn)

        # Tier 2: Secondary utility bar (Video | Export | View)
        utility_row = QHBoxLayout()
        utility_row.setSpacing(6)

        self.watch_video_btn = QPushButton("Watch Video", self)
        self.watch_video_btn.setIcon(make_icon("video", TOKENS.text_primary, 14))
        self.watch_video_btn.clicked.connect(self._on_watch_video)

        self.delete_video_btn = QPushButton("Delete Video", self)
        self.delete_video_btn.setObjectName("subtleDangerButton")
        self.delete_video_btn.setIcon(make_icon("trash", TOKENS.danger_fg, 14))
        self.delete_video_btn.clicked.connect(self._on_delete_video)

        self.export_json_btn = QPushButton("Export JSON", self)
        self.export_json_btn.setIcon(make_icon("export", TOKENS.text_primary, 14))
        self.export_json_btn.clicked.connect(self._on_export_json)

        self.export_pdf_btn = QPushButton("Export PDF", self)
        self.export_pdf_btn.setIcon(make_icon("export", TOKENS.text_primary, 14))
        self.export_pdf_btn.clicked.connect(self._on_export_pdf)

        self.show_all_steps_btn = QPushButton("Show All Steps", self)
        self.show_all_steps_btn.setCheckable(True)
        self.show_all_steps_btn.toggled.connect(self._refresh_timeline_table)

        utility_row.addWidget(self.watch_video_btn)
        utility_row.addWidget(self.delete_video_btn)
        utility_row.addWidget(make_vseparator(self))
        utility_row.addWidget(self.export_json_btn)
        utility_row.addWidget(self.export_pdf_btn)
        utility_row.addStretch(1)
        utility_row.addWidget(self.show_all_steps_btn)

        self._activity_bar = QProgressBar(self)
        self._activity_bar.setRange(0, 0)
        self._activity_bar.setTextVisible(False)
        self._activity_bar.hide()

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
        self.scenarios_list.setToolTip(
            "Click a scenario to filter the timeline. "
            "Hold Ctrl or Shift to select multiple for merging."
        )
        self.scenarios_list.itemSelectionChanged.connect(self._on_scenario_selection_changed)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.rename_btn = QPushButton("Rename", scenarios_pane)
        self.rename_btn.setIcon(make_icon("edit", TOKENS.text_primary, 13))
        self.rename_btn.clicked.connect(self._on_rename_scenario)
        self.toggle_include_btn = QPushButton("Exclude", scenarios_pane)
        self.toggle_include_btn.clicked.connect(self._on_toggle_include)
        self.delete_btn = QPushButton("Delete", scenarios_pane)
        self.delete_btn.setObjectName("subtleDangerButton")
        self.delete_btn.setIcon(make_icon("trash", TOKENS.danger_fg, 13))
        self.delete_btn.clicked.connect(self._on_delete_scenario)
        row1.addWidget(self.rename_btn)
        row1.addWidget(self.toggle_include_btn)
        row1.addWidget(self.delete_btn)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.merge_btn = QPushButton("Merge", scenarios_pane)
        self.merge_btn.setIcon(make_icon("merge", TOKENS.text_primary, 13))
        self.merge_btn.clicked.connect(self._on_merge_scenarios)
        self.split_btn = QPushButton("Split Here", scenarios_pane)
        self.split_btn.setIcon(make_icon("split", TOKENS.text_primary, 13))
        self.split_btn.clicked.connect(self._on_split_scenario)
        self.move_up_btn = QPushButton("Up", scenarios_pane)
        self.move_up_btn.setIcon(make_icon("up", TOKENS.text_primary, 13))
        self.move_up_btn.clicked.connect(lambda: self._on_move_scenario(-1))
        self.move_down_btn = QPushButton("Down", scenarios_pane)
        self.move_down_btn.setIcon(make_icon("down", TOKENS.text_primary, 13))
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

        self.timeline_table = QTableWidget(0, 6, timeline_pane)
        self.timeline_table.setHorizontalHeaderLabels(
            ["#", "Time", "Action", "Summary", "Correlated Outcomes", "Evidence"]
        )
        self.timeline_table.setAlternatingRowColors(True)
        self.timeline_table.setShowGrid(False)
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
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
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
        inspector_title.setObjectName("sectionTitle")
        self.evidence_inspector = QPlainTextEdit(inspector_pane)
        self.evidence_inspector.setObjectName("codePreview")
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
        splitter.setSizes([290, 660])

        layout.addWidget(primary_strip)
        layout.addLayout(utility_row)
        layout.addWidget(self._activity_bar)
        layout.addWidget(splitter, stretch=1)

    def load_session(self, session_id: str | None, session_name: str = "") -> None:
        """Load persisted processed events and scenarios for the given session."""
        self._session_id = session_id
        self._session_name = session_name
        self._uncorrelated_outcomes = []
        self._video_artifacts = []
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

        self._refresh_video_artifacts()
        self._reload_from_storage()

    def process_current_session(self) -> None:
        """Run deterministic processing and scenario detection off the UI thread."""
        if self._session_id is None or self._worker is not None or self._external_busy:
            return
        worker = SessionProcessingWorker(self._scenario_service, self._session_id, parent=self)
        worker.processing_finished.connect(self._on_processing_finished)
        worker.processing_failed.connect(self._on_processing_failed)
        self._worker = worker
        label = self._session_name or self._session_id[:8]
        self.summary_label.setText(f"{label}: Processing raw events...")
        self._update_controls()
        self.processing_started.emit()
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
            self.processing_finished.emit()
            return
        if not isinstance(result_obj, ProcessingResult) or not isinstance(scenarios_obj, list):
            self._update_controls()
            self.processing_finished.emit()
            return

        scenarios: list[Scenario] = scenarios_obj
        self._timeline = result_obj.processed_events
        self._scenarios = scenarios
        self._uncorrelated_outcomes = list(result_obj.uncorrelated_outcomes)
        self._render_scenarios(select_index=0 if scenarios else -1)
        self._refresh_timeline_table()
        uncorr_note = (
            f", {len(self._uncorrelated_outcomes)} uncorrelated"
            if self._uncorrelated_outcomes
            else ""
        )
        self.summary_label.setText(
            f"{self._session_name or self._session_id[:8]}: "
            f"{result_obj.raw_event_count} raw -> {len(result_obj.processed_events)} steps -> "
            f"{len(scenarios)} scenario(s) ({result_obj.dropped_count} filtered{uncorr_note})"
        )
        self._update_controls()
        self.processing_finished.emit()
        self.scenarios_changed.emit(self._session_id)

    def _on_processing_failed(self, session_id: str, error_message: str) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()

        self._update_controls()
        self.processing_finished.emit()
        if session_id == self._session_id:
            QMessageBox.warning(self, "Processing Failed", error_message)

    def _reload_from_storage(
        self,
        select_scenario_id: str | None = None,
        *,
        emit_changed: bool = False,
    ) -> None:
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
        if emit_changed:
            self.scenarios_changed.emit(self._session_id)
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
            if not scenario.included:
                item.setForeground(QColor(TOKENS.text_muted))
            inc_note = (
                "Included in generated tests" if scenario.included else "Excluded from generation"
            )
            item.setToolTip(f"{scenario.name}\n{step_count} step(s) — {inc_note}")
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
            time_text = event.started_at.strftime("%H:%M:%S.%f")[:-3]

            action_item = QTableWidgetItem(event.action_type.value)
            action_item.setForeground(QColor(TOKENS.accent_hover))
            outcomes_item = QTableWidgetItem(outcomes_text)
            if event.outcomes:
                outcomes_item.setForeground(QColor(TOKENS.info_fg))
            else:
                outcomes_item.setForeground(QColor(TOKENS.text_muted))

            self.timeline_table.setItem(row_idx, 0, QTableWidgetItem(str(event.order_index)))
            self.timeline_table.setItem(row_idx, 1, QTableWidgetItem(time_text))
            self.timeline_table.setItem(row_idx, 2, action_item)
            self.timeline_table.setItem(row_idx, 3, QTableWidgetItem(event.summary))
            self.timeline_table.setItem(row_idx, 4, outcomes_item)
            self.timeline_table.setItem(row_idx, 5, QTableWidgetItem(evidence_text))

        self._render_default_inspector()
        self._update_controls()

    def _render_default_inspector(self) -> None:
        if self._uncorrelated_outcomes:
            payload = {
                "uncorrelated_outcomes": [
                    {
                        "type": o.outcome_type.value,
                        "summary": o.summary,
                        "timestamp": o.timestamp.isoformat(),
                        "details": o.details,
                        "raw_event_ids": o.raw_event_ids,
                    }
                    for o in self._uncorrelated_outcomes
                ]
            }
            self.evidence_inspector.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            self.evidence_inspector.clear()

    def _on_timeline_selection_changed(self) -> None:
        row = self.timeline_table.currentRow()
        if 0 <= row < len(self._visible_events):
            event = self._visible_events[row]
            details = {
                "step_id": event.id,
                "action_type": event.action_type.value,
                "summary": event.summary,
                "started_at": event.started_at.isoformat(),
                "ended_at": event.ended_at.isoformat(),
                "page_id": event.page_id,
                "target": event.target,
                "payload": event.payload,
                "outcomes": [
                    {
                        "type": o.outcome_type.value,
                        "summary": o.summary,
                        "timestamp": o.timestamp.isoformat(),
                        "details": o.details,
                        "raw_event_ids": o.raw_event_ids,
                    }
                    for o in event.outcomes
                ],
                "raw_event_ids": event.raw_event_ids,
            }
            self.evidence_inspector.setPlainText(json.dumps(details, indent=2, ensure_ascii=False))
        else:
            self._render_default_inspector()
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
        self._reload_from_storage(select_scenario_id=updated.id, emit_changed=True)

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
        self._reload_from_storage(select_scenario_id=updated.id, emit_changed=True)

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
        self._reload_from_storage(emit_changed=True)

    def _on_merge_scenarios(self) -> None:
        selected = self._selected_scenarios()
        if len(selected) < 2:
            return
        try:
            merged = self._scenario_service.merge_scenarios([s.id for s in selected])
        except ScenarioError as exc:
            QMessageBox.warning(self, "Could Not Merge Scenarios", str(exc))
            return
        self._reload_from_storage(select_scenario_id=merged.id, emit_changed=True)

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
        self._reload_from_storage(select_scenario_id=left.id, emit_changed=True)

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
        self._reload_from_storage(select_scenario_id=scenario.id, emit_changed=True)

    def _refresh_video_artifacts(self) -> None:
        self._video_artifacts = []
        if self._session_id is None or self._artifact_repo is None or self._artifacts is None:
            return
        records = self._artifact_repo.list_by_session(
            self._session_id,
            artifact_type="recording_video",
        )
        valid: list[ArtifactRecord] = []
        for rec in records:
            try:
                if self._artifacts.resolve_path(rec.relative_path).is_file():
                    valid.append(rec)
            except ValueError:
                continue
        self._video_artifacts = valid

    def _on_watch_video(self) -> None:
        self._refresh_video_artifacts()
        if not self._video_artifacts or self._artifacts is None:
            QMessageBox.information(
                self,
                "No Recording Video",
                "No recording video is available for this session.",
            )
            self._update_controls()
            return
        primary = max(self._video_artifacts, key=lambda rec: rec.size_bytes)
        video_path = self._artifacts.resolve_path(primary.relative_path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(video_path)))

    def _on_delete_video(self) -> None:
        if not self._video_artifacts or self._artifacts is None or self._artifact_repo is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete Recording Video",
            f"Delete {len(self._video_artifacts)} recorded video file(s) for this session?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for rec in self._video_artifacts:
            with suppress(OSError, ValueError):
                self._artifacts.delete_file(rec.relative_path)
            self._artifact_repo.delete(rec.id)
        self._refresh_video_artifacts()
        self._update_controls()

    def _on_export_json(self) -> None:
        if self._session_id is None or self._bundle_service is None:
            return
        default_name = f"{(self._session_name or 'recording').replace(' ', '_')}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Processed Recording (JSON)",
            default_name,
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return
        try:
            bundle = self._bundle_service.export_to_file(self._session_id, Path(file_path))
        except (RecordingBundleError, OSError) as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Recording Exported",
            f"Exported {len(bundle.processed_events)} step(s) and "
            f"{len(bundle.scenarios)} scenario(s) to:\n{file_path}",
        )

    def _on_export_pdf(self) -> None:
        if self._session_id is None or self._bundle_service is None:
            return
        default_name = f"{(self._session_name or 'recording_report').replace(' ', '_')}.pdf"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Recording Report (PDF)",
            default_name,
            "PDF Files (*.pdf);;All Files (*)",
        )
        if not file_path:
            return
        try:
            bundle = self._bundle_service.export_session(self._session_id)
            export_recording_pdf(bundle, Path(file_path))
        except (RecordingBundleError, OSError) as exc:
            QMessageBox.warning(self, "PDF Export Failed", str(exc))
            return
        QMessageBox.information(
            self,
            "PDF Report Exported",
            f"Saved professional report ({len(bundle.processed_events)} step(s)) to:\n{file_path}",
        )

    def _on_toggle_reproduce(self) -> None:
        if self._repro_worker is not None:
            self._repro_worker.request_cancel()
            set_button_state(
                self.reproduce_btn,
                False,
                enabled_tip="Cancel live reproduction.",
                disabled_tip="Stopping live reproduction...",
            )
            self.summary_label.setText("Stopping live reproduction...")
            return

        if self._session_id is None or not self._timeline or self._worker is not None:
            return
        start_url = ""
        viewport_w, viewport_h = 1280, 800
        if self._session_service is not None:
            session = self._session_service.get_session(self._session_id)
            if session is not None:
                start_url = session.start_url
                viewport_w = int(session.metadata.get("viewport_width", 1280) or 1280)
                viewport_h = int(session.metadata.get("viewport_height", 800) or 800)

        worker = RecordingReproductionWorker(
            self._session_id,
            list(self._timeline),
            start_url=start_url,
            viewport_width=max(320, viewport_w),
            viewport_height=max(240, viewport_h),
            headless=False,
            parent=self,
        )
        worker.step_completed.connect(self._on_repro_step_completed)
        worker.reproduction_finished.connect(self._on_repro_finished)
        worker.reproduction_failed.connect(self._on_repro_failed)
        self._repro_worker = worker
        self.summary_label.setText(f"Reproducing {len(self._timeline)} step(s) in live Chromium...")
        self._update_controls()
        self.processing_started.emit()
        worker.start()

    def _on_repro_step_completed(self, step_obj: object) -> None:
        if not isinstance(step_obj, ReproductionStepResult):
            return
        self.summary_label.setText(
            f"Reproducing step {step_obj.order_index}/{len(self._timeline)} "
            f"[{step_obj.status.upper()}]: {step_obj.description}"
        )

    def _on_repro_finished(self, summary_obj: object) -> None:
        worker = self._repro_worker
        self._repro_worker = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()
        self._update_controls()
        self.processing_finished.emit()
        if isinstance(summary_obj, ReproductionSummary):
            status_word = "Cancelled" if summary_obj.cancelled else "Completed"
            self.summary_label.setText(
                f"Reproduction {status_word}: {summary_obj.passed_steps} passed, "
                f"{summary_obj.failed_steps} failed, {summary_obj.skipped_steps} skipped "
                f"(of {summary_obj.total_steps} step(s))."
            )
            if summary_obj.failed_steps > 0:
                failed_lines = [
                    f"Step {s.order_index} ({s.description}): {s.error_message}"
                    for s in summary_obj.steps
                    if s.status == "failed"
                ]
                QMessageBox.warning(
                    self,
                    "Reproduction Finished with Failures",
                    f"{summary_obj.passed_steps} passed, {summary_obj.failed_steps} failed, "
                    f"{summary_obj.skipped_steps} skipped.\n\n" + "\n".join(failed_lines[:5]),
                )

    def _on_repro_failed(self, error_message: str) -> None:
        worker = self._repro_worker
        self._repro_worker = None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()
        self._update_controls()
        self.processing_finished.emit()
        QMessageBox.warning(self, "Reproduction Failed", error_message)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)

    def _update_controls(self) -> None:
        has_session = self._session_id is not None
        reproducing = self._repro_worker is not None
        busy = self._worker is not None or reproducing or self._external_busy
        selected = self._selected_scenarios()
        single = len(selected) == 1
        multi = len(selected) >= 2
        has_timeline = bool(self._timeline)
        has_video = bool(self._video_artifacts)

        self._activity_bar.setVisible(self._worker is not None or reproducing)

        busy_tip = "Wait for the active background task to finish."

        set_button_state(
            self.process_btn,
            has_session and not busy,
            enabled_tip="Normalize raw events, correlate outcomes, and detect logical scenarios.",
            disabled_tip=busy_tip if busy else "Select a recording session first.",
        )
        set_button_state(
            self.show_all_steps_btn,
            has_session and not busy and has_timeline,
            enabled_tip="Toggle between all session steps and only the selected scenario's steps.",
            disabled_tip=busy_tip if busy else "Process a session first to view timeline steps.",
        )
        set_button_state(
            self.watch_video_btn,
            has_session and has_video and not reproducing,
            enabled_tip="Open the recorded session video (.webm) in your default media player.",
            disabled_tip=(
                "Stop reproduction before watching video."
                if reproducing
                else "No recording video was captured for this session."
            ),
        )
        set_button_state(
            self.delete_video_btn,
            has_session and has_video and not busy,
            enabled_tip="Delete the recorded video file(s) for this session from disk.",
            disabled_tip=busy_tip if busy else "No recording video to delete.",
        )

        self.reproduce_btn.setText("Stop Replay" if reproducing else "Reproduce")
        self.reproduce_btn.setObjectName("dangerButton" if reproducing else "")
        self.reproduce_btn.setIcon(
            make_icon("stop", "#FFFFFF", 14)
            if reproducing
            else make_icon("replay", TOKENS.text_primary, 14)
        )
        self._repolish(self.reproduce_btn)
        set_button_state(
            self.reproduce_btn,
            reproducing or (has_session and has_timeline and not busy),
            enabled_tip=(
                "Cancel live browser reproduction."
                if reproducing
                else "Replay all processed steps live in headed Chromium to reproduce the issue."
            ),
            disabled_tip=(
                busy_tip if busy else "Process a session with at least one step to reproduce it."
            ),
        )

        can_export = has_session and has_timeline and not busy and self._bundle_service is not None
        set_button_state(
            self.export_json_btn,
            can_export,
            enabled_tip=(
                "Export this processed recording and its scenarios as a portable JSON bundle."
            ),
            disabled_tip=busy_tip if busy else "Process a session first to export JSON.",
        )
        set_button_state(
            self.export_pdf_btn,
            can_export,
            enabled_tip=(
                "Generate a formatted A4 PDF report of this recording's steps and scenarios."
            ),
            disabled_tip=busy_tip if busy else "Process a session first to export a PDF report.",
        )

        set_button_state(
            self.rename_btn,
            single and not busy,
            enabled_tip="Rename the selected scenario.",
            disabled_tip=busy_tip if busy else "Select a single scenario to rename.",
        )
        set_button_state(
            self.toggle_include_btn,
            single and not busy,
            enabled_tip="Toggle whether this scenario is included in generated test definitions.",
            disabled_tip=busy_tip if busy else "Select a single scenario to include or exclude.",
        )
        set_button_state(
            self.delete_btn,
            single and not busy,
            enabled_tip="Remove the selected scenario from this session.",
            disabled_tip=busy_tip if busy else "Select a single scenario to delete.",
        )
        set_button_state(
            self.merge_btn,
            multi and not busy,
            enabled_tip="Merge the selected scenarios into a single continuous scenario.",
            disabled_tip=(
                busy_tip if busy else "Hold Ctrl or Shift and select 2 or more scenarios to merge."
            ),
        )

        if single and not busy:
            sc = selected[0]
            self.toggle_include_btn.setText("Exclude" if sc.included else "Include")
            idx = self._scenarios.index(sc)
            set_button_state(
                self.move_up_btn,
                idx > 0,
                enabled_tip="Move this scenario earlier in execution order.",
                disabled_tip="Scenario is already first.",
            )
            set_button_state(
                self.move_down_btn,
                idx < len(self._scenarios) - 1,
                enabled_tip="Move this scenario later in execution order.",
                disabled_tip="Scenario is already last.",
            )
            can_split = (
                not self.show_all_steps_btn.isChecked()
                and len(sc.processed_event_ids) >= 2
                and self.timeline_table.currentRow() >= 1
            )
            split_reason = (
                "Turn off 'Show All Steps' to split a scenario."
                if self.show_all_steps_btn.isChecked()
                else (
                    "Scenario needs at least 2 steps to split."
                    if len(sc.processed_event_ids) < 2
                    else "Select step 2 or later in the timeline table to split here."
                )
            )
            set_button_state(
                self.split_btn,
                can_split,
                enabled_tip="Split this scenario into two starting at the selected timeline step.",
                disabled_tip=split_reason,
            )
        else:
            self.toggle_include_btn.setText("Exclude")
            set_button_state(
                self.move_up_btn,
                False,
                enabled_tip="Move scenario up.",
                disabled_tip=busy_tip if busy else "Select a single scenario to reorder.",
            )
            set_button_state(
                self.move_down_btn,
                False,
                enabled_tip="Move scenario down.",
                disabled_tip=busy_tip if busy else "Select a single scenario to reorder.",
            )
            set_button_state(
                self.split_btn,
                False,
                enabled_tip="Split scenario at selected step.",
                disabled_tip=(
                    busy_tip
                    if busy
                    else "Select a single scenario and pick step 2+ in the timeline to split."
                ),
            )
