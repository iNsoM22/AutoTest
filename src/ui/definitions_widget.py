"""Test Definitions Editor and Generated Code Review widget for Phase 4."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.definitions import (
    AssertionConfidence,
    AssertionType,
    TestAssertion,
    TestDefinition,
    TestDefinitionError,
    TestDefinitionService,
    TestExecutionSettings,
    parse_selector_expression,
)
from src.export import CopyExportService
from src.processing import ProcessedActionType


class DefinitionsWidget(QWidget):
    """Interactive editor for Layer 3 test definitions and live Pytest/Playwright code preview."""

    definitions_changed = Signal(str)

    def __init__(
        self,
        definition_service: TestDefinitionService,
        copy_service: CopyExportService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._definition_service = definition_service
        self._copy_service = copy_service or CopyExportService()
        self._session_id: str | None = None
        self._session_name: str = ""
        self._base_url: str = ""
        self._definitions: list[TestDefinition] = []

        self._build_ui()
        self._update_controls()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Top toolbar
        toolbar = QHBoxLayout()
        self.summary_label = QLabel(
            "Select a processed session to generate and edit test definitions.", self
        )
        self.summary_label.setObjectName("mutedLabel")

        self.include_journey_checkbox = QCheckBox("Include Journey Test", self)
        self.include_journey_checkbox.setChecked(True)

        self.generate_btn = QPushButton("Generate from Scenarios", self)
        self.generate_btn.setObjectName("primaryButton")
        self.generate_btn.clicked.connect(self.generate_for_current_session)

        toolbar.addWidget(self.summary_label, stretch=1)
        toolbar.addWidget(self.include_journey_checkbox)
        toolbar.addWidget(self.generate_btn)

        # Main horizontal splitter: Definitions list (left) | Editor + Code Preview (right)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left pane: Test definitions list
        left_pane = QWidget(splitter)
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(6)

        left_title = QLabel("Test Definitions", left_pane)
        left_title.setObjectName("sectionTitle")

        self.definitions_list = QListWidget(left_pane)
        self.definitions_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.definitions_list.itemSelectionChanged.connect(self._on_definition_selection_changed)

        def_btns = QHBoxLayout()
        self.rename_def_btn = QPushButton("Rename", left_pane)
        self.rename_def_btn.clicked.connect(self._on_rename_definition)
        self.edit_details_btn = QPushButton("Settings", left_pane)
        self.edit_details_btn.clicked.connect(self._on_edit_definition_settings)
        self.toggle_def_btn = QPushButton("Exclude", left_pane)
        self.toggle_def_btn.clicked.connect(self._on_toggle_definition_included)
        self.delete_def_btn = QPushButton("Delete", left_pane)
        self.delete_def_btn.clicked.connect(self._on_delete_definition)
        def_btns.addWidget(self.rename_def_btn)
        def_btns.addWidget(self.edit_details_btn)
        def_btns.addWidget(self.toggle_def_btn)
        def_btns.addWidget(self.delete_def_btn)

        left_layout.addWidget(left_title)
        left_layout.addWidget(self.definitions_list, stretch=1)
        left_layout.addLayout(def_btns)

        # Right vertical splitter: Actions/Assertions editor (top) + Generated Code (bottom)
        right_splitter = QSplitter(Qt.Orientation.Vertical, splitter)

        # Editor tabs
        self.editor_tabs = QTabWidget(right_splitter)

        # Actions tab
        actions_tab = QWidget(self.editor_tabs)
        actions_layout = QVBoxLayout(actions_tab)
        actions_layout.setContentsMargins(6, 6, 6, 6)
        actions_layout.setSpacing(6)

        self.actions_table = QTableWidget(0, 5, actions_tab)
        self.actions_table.setHorizontalHeaderLabels(
            ["#", "Action", "Selector", "Value / URL", "Evidence"]
        )
        self.actions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.actions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.actions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.actions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.actions_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.actions_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.actions_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.actions_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.actions_table.verticalHeader().setVisible(False)
        self.actions_table.itemSelectionChanged.connect(self._update_controls)

        action_btns = QHBoxLayout()
        self.edit_selector_btn = QPushButton("Edit Selector", actions_tab)
        self.edit_selector_btn.clicked.connect(self._on_edit_action_selector)
        self.edit_value_btn = QPushButton("Edit Value/URL", actions_tab)
        self.edit_value_btn.clicked.connect(self._on_edit_action_value)
        self.move_action_up_btn = QPushButton("Up", actions_tab)
        self.move_action_up_btn.clicked.connect(lambda: self._on_move_action(-1))
        self.move_action_down_btn = QPushButton("Down", actions_tab)
        self.move_action_down_btn.clicked.connect(lambda: self._on_move_action(1))
        self.delete_action_btn = QPushButton("Delete Step", actions_tab)
        self.delete_action_btn.clicked.connect(self._on_delete_action)
        action_btns.addWidget(self.edit_selector_btn)
        action_btns.addWidget(self.edit_value_btn)
        action_btns.addWidget(self.move_action_up_btn)
        action_btns.addWidget(self.move_action_down_btn)
        action_btns.addWidget(self.delete_action_btn)
        action_btns.addStretch(1)

        actions_layout.addWidget(self.actions_table, stretch=1)
        actions_layout.addLayout(action_btns)
        self.editor_tabs.addTab(actions_tab, "Actions")

        # Assertions tab
        assertions_tab = QWidget(self.editor_tabs)
        assertions_layout = QVBoxLayout(assertions_tab)
        assertions_layout.setContentsMargins(6, 6, 6, 6)
        assertions_layout.setSpacing(6)

        self.assertions_table = QTableWidget(0, 5, assertions_tab)
        self.assertions_table.setHorizontalHeaderLabels(
            ["On", "Confidence", "Type", "Description / Expected", "Review"]
        )
        self.assertions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.assertions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.assertions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assertions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.assertions_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.assertions_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.assertions_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.assertions_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.assertions_table.verticalHeader().setVisible(False)
        self.assertions_table.itemSelectionChanged.connect(self._update_controls)

        assertion_btns = QHBoxLayout()
        self.toggle_assertion_btn = QPushButton("Toggle On/Off", assertions_tab)
        self.toggle_assertion_btn.clicked.connect(self._on_toggle_assertion)
        self.add_assertion_btn = QPushButton("+ Add Assertion", assertions_tab)
        self.add_assertion_btn.clicked.connect(self._on_add_assertion)
        self.edit_assertion_btn = QPushButton("Edit Expected", assertions_tab)
        self.edit_assertion_btn.clicked.connect(self._on_edit_assertion)
        self.delete_assertion_btn = QPushButton("Delete Assertion", assertions_tab)
        self.delete_assertion_btn.clicked.connect(self._on_delete_assertion)
        assertion_btns.addWidget(self.toggle_assertion_btn)
        assertion_btns.addWidget(self.add_assertion_btn)
        assertion_btns.addWidget(self.edit_assertion_btn)
        assertion_btns.addWidget(self.delete_assertion_btn)
        assertion_btns.addStretch(1)

        assertions_layout.addWidget(self.assertions_table, stretch=1)
        assertions_layout.addLayout(assertion_btns)
        self.editor_tabs.addTab(assertions_tab, "Assertions")

        # Bottom pane: Generated Pytest + Playwright Code Preview & Copy Bar
        code_pane = QWidget(right_splitter)
        code_layout = QVBoxLayout(code_pane)
        code_layout.setContentsMargins(6, 4, 0, 0)
        code_layout.setSpacing(6)

        code_header = QHBoxLayout()
        code_title = QLabel("Generated Pytest + Playwright Code", code_pane)
        code_title.setObjectName("sectionTitle")
        self.copy_status_label = QLabel("", code_pane)
        self.copy_status_label.setObjectName("mutedLabel")
        code_header.addWidget(code_title)
        code_header.addStretch(1)
        code_header.addWidget(self.copy_status_label)

        self.code_preview = QPlainTextEdit(code_pane)
        self.code_preview.setReadOnly(True)
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.code_preview.setFont(mono)
        self.code_preview.setPlaceholderText(
            "Generate test definitions from reviewed scenarios to preview standalone Pytest code."
        )

        copy_bar = QHBoxLayout()
        self.copy_test_btn = QPushButton("Copy Test", code_pane)
        self.copy_test_btn.clicked.connect(self._on_copy_single_test)
        self.copy_scenario_btn = QPushButton("Copy Scenario", code_pane)
        self.copy_scenario_btn.clicked.connect(self._on_copy_scenario)
        self.copy_selected_btn = QPushButton("Copy Selected", code_pane)
        self.copy_selected_btn.clicked.connect(self._on_copy_selected)
        self.copy_all_btn = QPushButton("Copy All Tests", code_pane)
        self.copy_all_btn.clicked.connect(self._on_copy_all)
        self.copy_fixtures_btn = QPushButton("Copy Fixtures & Config", code_pane)
        self.copy_fixtures_btn.clicked.connect(self._on_copy_fixtures)
        self.export_suite_btn = QPushButton("Export Suite...", code_pane)
        self.export_suite_btn.clicked.connect(self._on_export_suite)

        copy_bar.addWidget(self.copy_test_btn)
        copy_bar.addWidget(self.copy_scenario_btn)
        copy_bar.addWidget(self.copy_selected_btn)
        copy_bar.addWidget(self.copy_all_btn)
        copy_bar.addWidget(self.copy_fixtures_btn)
        copy_bar.addWidget(self.export_suite_btn)
        copy_bar.addStretch(1)

        code_layout.addLayout(code_header)
        code_layout.addWidget(self.code_preview, stretch=1)
        code_layout.addLayout(copy_bar)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)

        splitter.addWidget(left_pane)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 620])

        layout.addLayout(toolbar)
        layout.addWidget(splitter, stretch=1)

    def load_session(
        self,
        session_id: str | None,
        session_name: str = "",
        base_url: str = "",
    ) -> None:
        """Load persisted test definitions for the selected recording session."""
        self._session_id = session_id
        self._session_name = session_name
        self._base_url = base_url
        self.copy_status_label.setText("")

        if session_id is None:
            self._definitions = []
            self.definitions_list.clear()
            self.actions_table.setRowCount(0)
            self.assertions_table.setRowCount(0)
            self.code_preview.clear()
            self.summary_label.setText(
                "Select a processed session to generate and edit test definitions."
            )
            self._update_controls()
            return

        self._reload_from_storage()

    def generate_for_current_session(self) -> None:
        """Generate editable test definitions from the session's included scenarios."""
        if self._session_id is None:
            return
        try:
            definitions = self._definition_service.generate_from_session(
                self._session_id,
                include_journey=self.include_journey_checkbox.isChecked(),
            )
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Generate Tests", str(exc))
            return

        self._definitions = definitions
        self._render_definitions_list(select_index=0 if definitions else -1)
        self._refresh_selected_details()
        label = self._session_name or self._session_id[:8]
        self.summary_label.setText(
            f"{label}: Generated {len(definitions)} editable test definition(s)."
        )
        self._update_controls()
        self.definitions_changed.emit(self._session_id)

    def _reload_from_storage(self, select_definition_id: str | None = None) -> None:
        if self._session_id is None:
            return
        self._definitions = self._definition_service.list_by_session(self._session_id)
        select_idx = 0 if self._definitions else -1
        if select_definition_id is not None:
            for idx, item in enumerate(self._definitions):
                if item.id == select_definition_id:
                    select_idx = idx
                    break
        self._render_definitions_list(select_index=select_idx)
        self._refresh_selected_details()
        label = self._session_name or self._session_id[:8]
        if self._definitions:
            self.summary_label.setText(f"{label}: {len(self._definitions)} test definition(s).")
        else:
            self.summary_label.setText(
                f"{label}: No test definitions yet. Click 'Generate from Scenarios'."
            )
        self._update_controls()
        if select_definition_id is not None and self._session_id is not None:
            self.definitions_changed.emit(self._session_id)

    def _render_definitions_list(self, select_index: int = -1) -> None:
        self.definitions_list.blockSignals(True)
        self.definitions_list.clear()
        for defn in self._definitions:
            mode_tag = "Journey" if defn.mode.value == "journey" else "Independent"
            inc_tag = "" if defn.included else " [Excluded]"
            label = (
                f"{defn.order_index}. [{mode_tag}]{inc_tag} {defn.name} "
                f"({len(defn.actions)} steps, {len(defn.assertions)} asserts)"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, defn.id)
            self.definitions_list.addItem(item)

        if 0 <= select_index < self.definitions_list.count():
            self.definitions_list.setCurrentRow(select_index)
        self.definitions_list.blockSignals(False)

    def _selected_definitions(self) -> list[TestDefinition]:
        selected_ids = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in self.definitions_list.selectedItems()
        }
        return [d for d in self._definitions if d.id in selected_ids]

    def _current_definition(self) -> TestDefinition | None:
        selected = self._selected_definitions()
        return selected[0] if len(selected) == 1 else None

    def _on_definition_selection_changed(self) -> None:
        self._refresh_selected_details()
        self._update_controls()

    def _refresh_selected_details(self) -> None:
        selected = self._selected_definitions()
        if len(selected) == 1:
            defn = selected[0]
            self._populate_actions_table(defn)
            self._populate_assertions_table(defn)
            self.code_preview.setPlainText(self._copy_service.copy_single_test(defn))
        elif len(selected) > 1:
            self.actions_table.setRowCount(0)
            self.assertions_table.setRowCount(0)
            self.code_preview.setPlainText(self._copy_service.copy_selected_tests(selected))
        else:
            self.actions_table.setRowCount(0)
            self.assertions_table.setRowCount(0)
            self.code_preview.clear()

    def _populate_actions_table(self, defn: TestDefinition) -> None:
        self.actions_table.setRowCount(len(defn.actions))
        for row_idx, action in enumerate(defn.actions):
            selector_text = action.selector.display() if action.selector else "-"
            if action.url:
                val_text = action.url
            elif action.is_redacted:
                val_text = "[REDACTED -> env var]"
            elif action.checked is not None:
                val_text = f"checked={action.checked}"
            elif action.key:
                val_text = f"key={action.key}"
            else:
                val_text = action.value or "-"
            self.actions_table.setItem(row_idx, 0, QTableWidgetItem(str(action.order_index)))
            self.actions_table.setItem(row_idx, 1, QTableWidgetItem(action.action_type.value))
            self.actions_table.setItem(row_idx, 2, QTableWidgetItem(selector_text))
            self.actions_table.setItem(row_idx, 3, QTableWidgetItem(val_text))
            self.actions_table.setItem(
                row_idx, 4, QTableWidgetItem(f"{len(action.raw_event_ids)} raw")
            )

    def _populate_assertions_table(self, defn: TestDefinition) -> None:
        self.assertions_table.setRowCount(len(defn.assertions))
        for row_idx, assertion in enumerate(defn.assertions):
            on_text = "Yes" if assertion.enabled else "Off"
            review_text = "Review" if assertion.needs_review else "OK"
            detail = assertion.description
            if assertion.expected and assertion.expected not in detail:
                detail = f"{detail} ({assertion.expected})"
            self.assertions_table.setItem(row_idx, 0, QTableWidgetItem(on_text))
            self.assertions_table.setItem(row_idx, 1, QTableWidgetItem(assertion.confidence.value))
            self.assertions_table.setItem(
                row_idx, 2, QTableWidgetItem(assertion.assertion_type.value)
            )
            self.assertions_table.setItem(row_idx, 3, QTableWidgetItem(detail))
            self.assertions_table.setItem(row_idx, 4, QTableWidgetItem(review_text))

    # --- Definition editing handlers ---

    def _on_rename_definition(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Test", "Test name:", text=defn.name)
        if not ok:
            return
        try:
            updated = self._definition_service.update_metadata(defn.id, name=new_name)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Rename Test", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_edit_definition_settings(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Test Settings — {defn.name}")
        form = QFormLayout(dialog)

        desc_input = QLineEdit(defn.description, dialog)
        base_url_input = QLineEdit(defn.settings.base_url, dialog)
        timeout_spin = QSpinBox(dialog)
        timeout_spin.setRange(1000, 300000)
        timeout_spin.setSingleStep(1000)
        timeout_spin.setValue(defn.settings.timeout_ms)
        width_spin = QSpinBox(dialog)
        width_spin.setRange(320, 3840)
        width_spin.setValue(defn.settings.viewport_width)
        height_spin = QSpinBox(dialog)
        height_spin.setRange(240, 2160)
        height_spin.setValue(defn.settings.viewport_height)
        headless_chk = QCheckBox("Run headless by default", dialog)
        headless_chk.setChecked(defn.settings.headless)
        prereq_text = (
            "\n".join(f"- {p.name}: {p.description}" for p in defn.prerequisites)
            if defn.prerequisites
            else "(None)"
        )
        prereq_label = QLabel(prereq_text, dialog)
        prereq_label.setWordWrap(True)

        form.addRow("Description:", desc_input)
        form.addRow("Base URL:", base_url_input)
        form.addRow("Timeout (ms):", timeout_spin)
        form.addRow("Viewport Width:", width_spin)
        form.addRow("Viewport Height:", height_spin)
        form.addRow("", headless_chk)
        form.addRow("Prerequisites:", prereq_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_settings = TestExecutionSettings(
            timeout_ms=timeout_spin.value(),
            headless=headless_chk.isChecked(),
            viewport_width=width_spin.value(),
            viewport_height=height_spin.value(),
            base_url=base_url_input.text().strip(),
        )
        try:
            updated = self._definition_service.update_metadata(
                defn.id,
                description=desc_input.text().strip(),
                settings=new_settings,
            )
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Update Settings", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_toggle_definition_included(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        try:
            updated = self._definition_service.update_metadata(defn.id, included=not defn.included)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Update Test", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_delete_definition(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        try:
            self._definition_service.delete_definition(defn.id)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Delete Test", str(exc))
            return
        self._reload_from_storage()

    # --- Action editing handlers ---

    def _on_edit_action_selector(self) -> None:
        defn = self._current_definition()
        row = self.actions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.actions)):
            return
        action = defn.actions[row]
        current = action.selector.display() if action.selector else ""
        expr, ok = QInputDialog.getText(
            self,
            "Edit Selector",
            "Selector (e.g. test_id=login-btn, role=button[name='Sign in'], text=Save, or #id):",
            text=current,
        )
        if not ok:
            return
        try:
            parsed = parse_selector_expression(expr)
            updated = self._definition_service.update_action(defn.id, action.id, selector=parsed)
        except (ValueError, TestDefinitionError) as exc:
            QMessageBox.warning(self, "Invalid Selector", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_edit_action_value(self) -> None:
        defn = self._current_definition()
        row = self.actions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.actions)):
            return
        action = defn.actions[row]
        if action.action_type == ProcessedActionType.CHECK:
            choice, ok = QInputDialog.getItem(
                self,
                "Edit Check State",
                "Checked state:",
                ["true", "false"],
                0 if action.checked is not False else 1,
                False,
            )
            if not ok:
                return
            try:
                updated = self._definition_service.update_action(
                    defn.id,
                    action.id,
                    checked=(choice == "true"),
                )
            except TestDefinitionError as exc:
                QMessageBox.warning(self, "Could Not Update Step", str(exc))
                return
            self._reload_from_storage(select_definition_id=updated.id)
            self.actions_table.selectRow(row)
            return

        is_nav = action.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB)
        is_key = action.action_type in (ProcessedActionType.PRESS_KEY, ProcessedActionType.SUBMIT)
        if is_nav:
            current, label = action.url, "URL:"
        elif is_key:
            current, label = action.key, "Key (leave empty to click/submit form):"
        elif action.is_redacted:
            current = ""
            label = "Value (leave empty to keep redacted env var):"
        else:
            current, label = action.value, "Value:"
        new_val, ok = QInputDialog.getText(self, "Edit Step Value", label, text=current)
        if not ok:
            return
        if action.is_redacted and not is_nav and not is_key and not new_val:
            return
        try:
            if is_nav:
                updated = self._definition_service.update_action(defn.id, action.id, url=new_val)
            elif is_key:
                updated = self._definition_service.update_action(defn.id, action.id, key=new_val)
            else:
                updated = self._definition_service.update_action(defn.id, action.id, value=new_val)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Update Step", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)
        self.actions_table.selectRow(row)

    def _on_move_action(self, delta: int) -> None:
        defn = self._current_definition()
        row = self.actions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.actions)):
            return
        action = defn.actions[row]
        try:
            updated = self._definition_service.move_action(defn.id, action.id, delta)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Move Step", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)
        new_row = max(0, min(row + delta, self.actions_table.rowCount() - 1))
        if self.actions_table.rowCount() > 0:
            self.actions_table.selectRow(new_row)

    def _on_delete_action(self) -> None:
        defn = self._current_definition()
        row = self.actions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.actions)):
            return
        action = defn.actions[row]
        try:
            updated = self._definition_service.delete_action(defn.id, action.id)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Delete Step", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    # --- Assertion editing handlers ---

    def _on_toggle_assertion(self) -> None:
        defn = self._current_definition()
        row = self.assertions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.assertions)):
            return
        assertion = defn.assertions[row]
        try:
            updated = self._definition_service.update_assertion(
                defn.id,
                assertion.id,
                enabled=not assertion.enabled,
                needs_review=False,
            )
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Update Assertion", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_add_assertion(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        type_choices = [
            AssertionType.TEXT_VISIBLE.value,
            AssertionType.URL_CONTAINS.value,
            AssertionType.URL_EQUALS.value,
            AssertionType.ELEMENT_VISIBLE.value,
            AssertionType.ELEMENT_CHECKED.value,
            AssertionType.ELEMENT_VALUE.value,
        ]
        chosen, ok = QInputDialog.getItem(
            self,
            "Add Assertion",
            "Assertion type:",
            type_choices,
            0,
            False,
        )
        if not ok:
            return
        atype = AssertionType(chosen)
        selector = None
        expected = ""

        if atype in (
            AssertionType.ELEMENT_VISIBLE,
            AssertionType.ELEMENT_CHECKED,
            AssertionType.ELEMENT_VALUE,
        ):
            expr, ok_sel = QInputDialog.getText(
                self,
                "Assertion Element Selector",
                "Selector (e.g. test_id=banner, #status, text=Done):",
            )
            if not ok_sel or not expr.strip():
                return
            try:
                selector = parse_selector_expression(expr)
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid Selector", str(exc))
                return

        if atype == AssertionType.ELEMENT_CHECKED:
            val, ok_val = QInputDialog.getItem(
                self, "Expected Checked State", "Checked:", ["true", "false"], 0, False
            )
            if not ok_val:
                return
            expected = val
            description = f"Verify '{selector.display() if selector else 'element'}' checked={val}"
        elif atype == AssertionType.ELEMENT_VISIBLE:
            description = f"Verify {selector.display() if selector else 'element'} is visible"
        else:
            prompt_label = {
                AssertionType.TEXT_VISIBLE: "Expected visible text:",
                AssertionType.URL_CONTAINS: "Expected URL fragment:",
                AssertionType.URL_EQUALS: "Expected exact URL:",
                AssertionType.ELEMENT_VALUE: "Expected element value:",
            }.get(atype, "Expected value:")
            text, ok_val = QInputDialog.getText(self, "Expected Value", prompt_label)
            if not ok_val or not text.strip():
                return
            expected = text.strip()
            if atype == AssertionType.TEXT_VISIBLE:
                description = f"Verify '{expected}' is visible"
            elif atype in (AssertionType.URL_CONTAINS, AssertionType.URL_EQUALS):
                description = f"Verify URL matches '{expected}'"
            else:
                description = (
                    f"Verify '{selector.display() if selector else 'element'}' "
                    f"has value '{expected}'"
                )

        assertion = TestAssertion(
            order_index=len(defn.assertions) + 1,
            assertion_type=atype,
            confidence=AssertionConfidence.DIRECTLY_OBSERVED,
            needs_review=False,
            enabled=True,
            description=description,
            selector=selector,
            expected=expected,
            evidence_summary="Added manually during review.",
        )
        try:
            updated = self._definition_service.add_assertion(defn.id, assertion)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Add Assertion", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    def _on_edit_assertion(self) -> None:
        defn = self._current_definition()
        row = self.assertions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.assertions)):
            return
        assertion = defn.assertions[row]
        if assertion.assertion_type == AssertionType.ELEMENT_VISIBLE:
            current_sel = assertion.selector.display() if assertion.selector else ""
            expr, ok = QInputDialog.getText(
                self,
                "Edit Assertion Selector",
                "Element selector (e.g. test_id=banner, #status, text=Done):",
                text=current_sel,
            )
            if not ok:
                return
            try:
                parsed = parse_selector_expression(expr)
                updated = self._definition_service.update_assertion(
                    defn.id,
                    assertion.id,
                    selector=parsed,
                    description=f"Verify {parsed.display()} is visible",
                    needs_review=False,
                )
            except (ValueError, TestDefinitionError) as exc:
                QMessageBox.warning(self, "Could Not Update Assertion", str(exc))
                return
            self._reload_from_storage(select_definition_id=updated.id)
            self.assertions_table.selectRow(row)
            return

        new_expected, ok = QInputDialog.getText(
            self,
            "Edit Assertion Expected Value",
            "Expected value:",
            text=assertion.expected,
        )
        if not ok:
            return
        cleaned = new_expected.strip()
        new_desc = assertion.description
        if assertion.assertion_type == AssertionType.TEXT_VISIBLE and cleaned:
            new_desc = f"Verify '{cleaned}' is visible"
        elif assertion.assertion_type in (AssertionType.URL_CONTAINS, AssertionType.URL_EQUALS):
            new_desc = f"Verify URL matches '{cleaned}'"
        elif assertion.assertion_type == AssertionType.ELEMENT_CHECKED and assertion.selector:
            new_desc = f"Verify '{assertion.selector.display()}' checked={cleaned}"
        elif assertion.assertion_type == AssertionType.ELEMENT_VALUE and assertion.selector:
            new_desc = f"Verify '{assertion.selector.display()}' has value '{cleaned}'"
        try:
            updated = self._definition_service.update_assertion(
                defn.id,
                assertion.id,
                expected=cleaned,
                description=new_desc,
                needs_review=False,
            )
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Update Assertion", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)
        self.assertions_table.selectRow(row)

    def _on_delete_assertion(self) -> None:
        defn = self._current_definition()
        row = self.assertions_table.currentRow()
        if defn is None or not (0 <= row < len(defn.assertions)):
            return
        assertion = defn.assertions[row]
        try:
            updated = self._definition_service.delete_assertion(defn.id, assertion.id)
        except TestDefinitionError as exc:
            QMessageBox.warning(self, "Could Not Delete Assertion", str(exc))
            return
        self._reload_from_storage(select_definition_id=updated.id)

    # --- Copy handlers ---

    def _copy_to_clipboard(self, code: str, label: str) -> None:
        if not code:
            self.copy_status_label.setText("Nothing to copy.")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)
        self.copy_status_label.setText(f"Copied {label} ({len(code.splitlines())} lines).")

    def _on_copy_single_test(self) -> None:
        defn = self._current_definition()
        if defn is None:
            return
        self._copy_to_clipboard(self._copy_service.copy_single_test(defn), f"'{defn.name}'")

    def _on_copy_scenario(self) -> None:
        defn = self._current_definition()
        if defn is None or defn.scenario_id is None:
            return
        code = self._copy_service.copy_scenario_tests(self._definitions, defn.scenario_id)
        self._copy_to_clipboard(code, f"scenario '{defn.name}'")

    def _on_copy_selected(self) -> None:
        selected = self._selected_definitions()
        if not selected:
            return
        code = self._copy_service.copy_selected_tests(selected)
        self._copy_to_clipboard(code, f"{len(selected)} selected test(s)")

    def _on_copy_all(self) -> None:
        code = self._copy_service.copy_all_tests(self._definitions)
        self._copy_to_clipboard(code, "all included tests")

    def _on_copy_fixtures(self) -> None:
        code = self._copy_service.copy_fixtures_and_config(
            self._definitions,
            base_url=self._base_url,
        )
        self._copy_to_clipboard(code, "fixtures & pytest.ini")

    def _on_export_suite(self) -> None:
        if not self._definitions:
            return
        target_dir = QFileDialog.getExistingDirectory(self, "Export Standalone Pytest Suite")
        if not target_dir:
            return
        try:
            written = self._copy_service.export_suite_to_dir(
                self._definitions,
                Path(target_dir),
                base_url=self._base_url,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))
            return
        self.copy_status_label.setText(f"Exported {len(written)} file(s) to {target_dir}.")

    def _update_controls(self) -> None:
        has_session = self._session_id is not None
        selected = self._selected_definitions()
        single = len(selected) == 1
        defn = selected[0] if single else None

        self.generate_btn.setEnabled(has_session)
        self.include_journey_checkbox.setEnabled(has_session)
        self.rename_def_btn.setEnabled(single)
        self.edit_details_btn.setEnabled(single)
        self.toggle_def_btn.setEnabled(single)
        self.delete_def_btn.setEnabled(single)
        if defn is not None:
            self.toggle_def_btn.setText("Exclude" if defn.included else "Include")
        else:
            self.toggle_def_btn.setText("Exclude")

        action_row = self.actions_table.currentRow()
        has_action = defn is not None and 0 <= action_row < len(defn.actions)
        self.edit_selector_btn.setEnabled(has_action)
        self.edit_value_btn.setEnabled(has_action)
        self.move_action_up_btn.setEnabled(has_action and action_row > 0)
        self.move_action_down_btn.setEnabled(
            has_action and defn is not None and action_row < len(defn.actions) - 1
        )
        self.delete_action_btn.setEnabled(has_action)

        assertion_row = self.assertions_table.currentRow()
        has_assertion = defn is not None and 0 <= assertion_row < len(defn.assertions)
        self.add_assertion_btn.setEnabled(single)
        self.toggle_assertion_btn.setEnabled(has_assertion)
        self.edit_assertion_btn.setEnabled(has_assertion)
        self.delete_assertion_btn.setEnabled(has_assertion)

        self.copy_test_btn.setEnabled(single)
        self.copy_scenario_btn.setEnabled(defn is not None and defn.scenario_id is not None)
        self.copy_selected_btn.setEnabled(bool(selected))
        self.copy_all_btn.setEnabled(any(d.included for d in self._definitions))
        self.copy_fixtures_btn.setEnabled(bool(self._definitions))
        self.export_suite_btn.setEnabled(any(d.included for d in self._definitions))
