"""Test Definitions Editor and Generated Code Review widget for Phase 4."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from src.ui.theme import (
    TOKENS,
    make_icon,
    make_vseparator,
    set_button_state,
    status_color,
)


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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Top toolbar card
        toolbar_frame = QFrame(self)
        toolbar_frame.setObjectName("toolbarStrip")
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(12, 8, 12, 8)
        toolbar.setSpacing(10)

        self.summary_label = QLabel(
            "Select a processed session to generate and edit test definitions.", toolbar_frame
        )
        self.summary_label.setObjectName("mutedLabel")

        self.include_journey_checkbox = QCheckBox("Include Journey Test", toolbar_frame)
        self.include_journey_checkbox.setChecked(True)
        self.include_journey_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.include_journey_checkbox.setToolTip(
            "Also generate a combined end-to-end journey test across all included scenarios."
        )

        self.generate_btn = QPushButton("Generate from Scenarios", toolbar_frame)
        self.generate_btn.setObjectName("primaryButton")
        self.generate_btn.setIcon(make_icon("spark", TOKENS.accent_fg, 14))
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
        self.definitions_list.setToolTip(
            "Select a test to edit its steps and assertions, "
            "or multi-select to preview combined code."
        )
        self.definitions_list.itemSelectionChanged.connect(self._on_definition_selection_changed)

        def_btns_row1 = QHBoxLayout()
        def_btns_row1.setSpacing(6)
        self.rename_def_btn = QPushButton("Rename", left_pane)
        self.rename_def_btn.setIcon(make_icon("edit", TOKENS.text_primary, 13))
        self.rename_def_btn.clicked.connect(self._on_rename_definition)
        self.edit_details_btn = QPushButton("Settings", left_pane)
        self.edit_details_btn.setIcon(make_icon("sliders", TOKENS.text_primary, 13))
        self.edit_details_btn.clicked.connect(self._on_edit_definition_settings)
        def_btns_row1.addWidget(self.rename_def_btn)
        def_btns_row1.addWidget(self.edit_details_btn)

        def_btns_row2 = QHBoxLayout()
        def_btns_row2.setSpacing(6)
        self.toggle_def_btn = QPushButton("Exclude", left_pane)
        self.toggle_def_btn.clicked.connect(self._on_toggle_definition_included)
        self.delete_def_btn = QPushButton("Delete", left_pane)
        self.delete_def_btn.setObjectName("subtleDangerButton")
        self.delete_def_btn.setIcon(make_icon("trash", TOKENS.danger_fg, 13))
        self.delete_def_btn.clicked.connect(self._on_delete_definition)
        def_btns_row2.addWidget(self.toggle_def_btn)
        def_btns_row2.addWidget(self.delete_def_btn)

        left_layout.addWidget(left_title)
        left_layout.addWidget(self.definitions_list, stretch=1)
        left_layout.addLayout(def_btns_row1)
        left_layout.addLayout(def_btns_row2)

        # Right vertical splitter: Actions/Assertions editor (top) + Generated Code (bottom)
        right_splitter = QSplitter(Qt.Orientation.Vertical, splitter)

        # Editor tabs
        self.editor_tabs = QTabWidget(right_splitter)
        self.editor_tabs.tabBar().setCursor(Qt.CursorShape.PointingHandCursor)

        # Actions tab
        actions_tab = QWidget(self.editor_tabs)
        actions_layout = QVBoxLayout(actions_tab)
        actions_layout.setContentsMargins(8, 8, 8, 8)
        actions_layout.setSpacing(8)

        self.actions_table = QTableWidget(0, 5, actions_tab)
        self.actions_table.setHorizontalHeaderLabels(
            ["#", "Action", "Selector", "Value / URL", "Evidence"]
        )
        self.actions_table.setAlternatingRowColors(True)
        self.actions_table.setShowGrid(False)
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
        action_btns.setSpacing(6)
        self.edit_selector_btn = QPushButton("Edit Selector", actions_tab)
        self.edit_selector_btn.setIcon(make_icon("edit", TOKENS.text_primary, 13))
        self.edit_selector_btn.clicked.connect(self._on_edit_action_selector)
        self.edit_value_btn = QPushButton("Edit Value/URL", actions_tab)
        self.edit_value_btn.clicked.connect(self._on_edit_action_value)
        self.move_action_up_btn = QPushButton("Up", actions_tab)
        self.move_action_up_btn.setIcon(make_icon("up", TOKENS.text_primary, 13))
        self.move_action_up_btn.clicked.connect(lambda: self._on_move_action(-1))
        self.move_action_down_btn = QPushButton("Down", actions_tab)
        self.move_action_down_btn.setIcon(make_icon("down", TOKENS.text_primary, 13))
        self.move_action_down_btn.clicked.connect(lambda: self._on_move_action(1))
        self.delete_action_btn = QPushButton("Delete Step", actions_tab)
        self.delete_action_btn.setObjectName("subtleDangerButton")
        self.delete_action_btn.setIcon(make_icon("trash", TOKENS.danger_fg, 13))
        self.delete_action_btn.clicked.connect(self._on_delete_action)
        action_btns.addWidget(self.edit_selector_btn)
        action_btns.addWidget(self.edit_value_btn)
        action_btns.addWidget(make_vseparator(actions_tab))
        action_btns.addWidget(self.move_action_up_btn)
        action_btns.addWidget(self.move_action_down_btn)
        action_btns.addStretch(1)
        action_btns.addWidget(self.delete_action_btn)

        actions_layout.addWidget(self.actions_table, stretch=1)
        actions_layout.addLayout(action_btns)
        self.editor_tabs.addTab(actions_tab, "Actions")

        # Assertions tab
        assertions_tab = QWidget(self.editor_tabs)
        assertions_layout = QVBoxLayout(assertions_tab)
        assertions_layout.setContentsMargins(8, 8, 8, 8)
        assertions_layout.setSpacing(8)

        self.assertions_table = QTableWidget(0, 5, assertions_tab)
        self.assertions_table.setHorizontalHeaderLabels(
            ["On", "Confidence", "Type", "Description / Expected", "Review"]
        )
        self.assertions_table.setAlternatingRowColors(True)
        self.assertions_table.setShowGrid(False)
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
        assertion_btns.setSpacing(6)
        self.add_assertion_btn = QPushButton("Add Assertion", assertions_tab)
        self.add_assertion_btn.setObjectName("primaryButton")
        self.add_assertion_btn.setIcon(make_icon("plus", TOKENS.accent_fg, 13))
        self.add_assertion_btn.clicked.connect(self._on_add_assertion)
        self.toggle_assertion_btn = QPushButton("Toggle On/Off", assertions_tab)
        self.toggle_assertion_btn.clicked.connect(self._on_toggle_assertion)
        self.edit_assertion_btn = QPushButton("Edit Expected", assertions_tab)
        self.edit_assertion_btn.setIcon(make_icon("edit", TOKENS.text_primary, 13))
        self.edit_assertion_btn.clicked.connect(self._on_edit_assertion)
        self.delete_assertion_btn = QPushButton("Delete Assertion", assertions_tab)
        self.delete_assertion_btn.setObjectName("subtleDangerButton")
        self.delete_assertion_btn.setIcon(make_icon("trash", TOKENS.danger_fg, 13))
        self.delete_assertion_btn.clicked.connect(self._on_delete_assertion)
        assertion_btns.addWidget(self.add_assertion_btn)
        assertion_btns.addWidget(self.toggle_assertion_btn)
        assertion_btns.addWidget(self.edit_assertion_btn)
        assertion_btns.addStretch(1)
        assertion_btns.addWidget(self.delete_assertion_btn)

        assertions_layout.addWidget(self.assertions_table, stretch=1)
        assertions_layout.addLayout(assertion_btns)
        self.editor_tabs.addTab(assertions_tab, "Assertions")

        # Bottom pane: Generated Pytest + Playwright Code Preview & Copy Bar
        code_pane = QWidget(right_splitter)
        code_layout = QVBoxLayout(code_pane)
        code_layout.setContentsMargins(6, 6, 0, 0)
        code_layout.setSpacing(6)

        code_header = QHBoxLayout()
        code_title = QLabel("Generated Pytest + Playwright Code", code_pane)
        code_title.setObjectName("sectionTitle")
        self.copy_status_label = QLabel("", code_pane)
        self.copy_status_label.setObjectName("successInline")
        code_header.addWidget(code_title)
        code_header.addStretch(1)
        code_header.addWidget(self.copy_status_label)

        self.code_preview = QPlainTextEdit(code_pane)
        self.code_preview.setObjectName("codePreview")
        self.code_preview.setReadOnly(True)
        mono = QFont("Cascadia Code", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.code_preview.setFont(mono)
        self.code_preview.setPlaceholderText(
            "Generate test definitions from reviewed scenarios to preview standalone Pytest code."
        )

        copy_bar = QHBoxLayout()
        copy_bar.setSpacing(6)
        self.copy_test_btn = QPushButton("Copy Test", code_pane)
        self.copy_test_btn.setIcon(make_icon("copy", TOKENS.text_primary, 13))
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
        self.export_suite_btn.setObjectName("primaryButton")
        self.export_suite_btn.setIcon(make_icon("folder", TOKENS.accent_fg, 13))
        self.export_suite_btn.clicked.connect(self._on_export_suite)

        copy_bar.addWidget(self.copy_test_btn)
        copy_bar.addWidget(self.copy_scenario_btn)
        copy_bar.addWidget(self.copy_selected_btn)
        copy_bar.addWidget(self.copy_all_btn)
        copy_bar.addWidget(self.copy_fixtures_btn)
        copy_bar.addStretch(1)
        copy_bar.addWidget(self.export_suite_btn)

        code_layout.addLayout(code_header)
        code_layout.addWidget(self.code_preview, stretch=1)
        code_layout.addLayout(copy_bar)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)

        splitter.addWidget(left_pane)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([290, 660])

        layout.addWidget(toolbar_frame)
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

    def set_base_url(self, base_url: str) -> None:
        """Update the project base URL used for fixture copy and suite export."""
        self._base_url = base_url

    def refresh(self, session_id: str | None = None) -> None:
        """Reload persisted definitions if the active session matches `session_id`."""
        if self._session_id is None:
            return
        if session_id is not None and session_id != self._session_id:
            return
        self._reload_from_storage()

    def _reload_from_storage(
        self,
        select_definition_id: str | None = None,
        *,
        emit_changed: bool = False,
    ) -> None:
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
        if (emit_changed or select_definition_id is not None) and self._session_id is not None:
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
            if not defn.included:
                item.setForeground(QColor(TOKENS.text_muted))
            elif defn.mode.value == "journey":
                item.setForeground(QColor(TOKENS.info_fg))
            item.setToolTip(
                f"{defn.name} ({mode_tag})\n"
                f"{len(defn.actions)} step(s), {len(defn.assertions)} assertion(s)"
            )
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

            action_item = QTableWidgetItem(action.action_type.value)
            action_item.setForeground(QColor(TOKENS.accent_hover))
            val_item = QTableWidgetItem(val_text)
            if action.is_redacted:
                val_item.setForeground(QColor(TOKENS.warning_fg))

            self.actions_table.setItem(row_idx, 0, QTableWidgetItem(str(action.order_index)))
            self.actions_table.setItem(row_idx, 1, action_item)
            self.actions_table.setItem(row_idx, 2, QTableWidgetItem(selector_text))
            self.actions_table.setItem(row_idx, 3, val_item)
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

            on_item = QTableWidgetItem(on_text)
            on_item.setForeground(status_color(on_text))
            review_item = QTableWidgetItem(review_text)
            review_item.setForeground(status_color(review_text))

            self.assertions_table.setItem(row_idx, 0, on_item)
            self.assertions_table.setItem(row_idx, 1, QTableWidgetItem(assertion.confidence.value))
            self.assertions_table.setItem(
                row_idx, 2, QTableWidgetItem(assertion.assertion_type.value)
            )
            self.assertions_table.setItem(row_idx, 3, QTableWidgetItem(detail))
            self.assertions_table.setItem(row_idx, 4, review_item)

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
        dialog.setWindowIcon(make_icon("brand", size=24))
        dialog.setMinimumWidth(460)
        form = QFormLayout(dialog)
        form.setContentsMargins(18, 18, 18, 16)
        form.setSpacing(10)

        desc_input = QLineEdit(defn.description, dialog)
        desc_input.setPlaceholderText("Optional docstring/description for this test")
        base_url_input = QLineEdit(defn.settings.base_url, dialog)
        base_url_input.setPlaceholderText("https://example.com")
        timeout_spin = QSpinBox(dialog)
        timeout_spin.setRange(1000, 300000)
        timeout_spin.setSingleStep(1000)
        timeout_spin.setValue(defn.settings.timeout_ms)
        timeout_spin.setToolTip("Playwright action/assertion timeout in milliseconds.")
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
        prereq_label.setObjectName("mutedLabel")
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
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Save Settings")
            ok_btn.setObjectName("primaryButton")
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
        self._reload_from_storage(emit_changed=True)

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

        if assertion.assertion_type == AssertionType.ELEMENT_CHECKED:
            choice, ok = QInputDialog.getItem(
                self,
                "Edit Checked Assertion",
                "Expected checked state:",
                ["true", "false"],
                0 if assertion.expected.lower() != "false" else 1,
                False,
            )
            if not ok:
                return
            cleaned = choice
        else:
            new_expected, ok = QInputDialog.getText(
                self,
                "Edit Assertion Expected Value",
                "Expected value:",
                text=assertion.expected,
            )
            if not ok:
                return
            cleaned = new_expected.strip()
            if (
                assertion.assertion_type
                in (
                    AssertionType.TEXT_VISIBLE,
                    AssertionType.URL_CONTAINS,
                    AssertionType.URL_EQUALS,
                )
                and not cleaned
            ):
                QMessageBox.warning(
                    self,
                    "Invalid Expected Value",
                    "Expected value cannot be empty for this assertion type.",
                )
                return
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

    def set_external_busy(self, busy: bool) -> None:
        """Lock/unlock editing controls while another tab holds an active worker."""
        if getattr(self, "_external_busy", False) != busy:
            self._external_busy = busy
            self._update_controls()

    def _update_controls(self) -> None:
        has_session = self._session_id is not None
        busy = getattr(self, "_external_busy", False)
        busy_tip = "Wait for the active background task to finish."
        selected = self._selected_definitions()
        single = len(selected) == 1
        defn = selected[0] if single else None
        has_included = any(d.included for d in self._definitions)

        set_button_state(
            self.generate_btn,
            has_session and not busy,
            enabled_tip=(
                "Generate editable Playwright + Pytest definitions "
                "from this session's included scenarios."
            ),
            disabled_tip=busy_tip if busy else "Select a recording session first.",
        )
        self.include_journey_checkbox.setEnabled(has_session and not busy)

        set_button_state(
            self.rename_def_btn,
            single and not busy,
            enabled_tip="Rename the selected test definition.",
            disabled_tip=busy_tip if busy else "Select a single test definition to rename.",
        )
        set_button_state(
            self.edit_details_btn,
            single and not busy,
            enabled_tip=(
                "Configure timeout, viewport size, headless default, and base URL for this test."
            ),
            disabled_tip=busy_tip if busy else "Select a single test definition to edit settings.",
        )
        set_button_state(
            self.toggle_def_btn,
            single and not busy,
            enabled_tip="Include or exclude this test from full suite copy and export.",
            disabled_tip=busy_tip
            if busy
            else "Select a single test definition to toggle inclusion.",
        )
        set_button_state(
            self.delete_def_btn,
            single and not busy,
            enabled_tip="Permanently delete the selected test definition.",
            disabled_tip=busy_tip if busy else "Select a single test definition to delete.",
        )
        if defn is not None:
            self.toggle_def_btn.setText("Exclude" if defn.included else "Include")
        else:
            self.toggle_def_btn.setText("Exclude")

        action_row = self.actions_table.currentRow()
        has_action = defn is not None and 0 <= action_row < len(defn.actions)
        current_action = defn.actions[action_row] if (has_action and defn is not None) else None
        is_nav_action = current_action is not None and current_action.action_type in (
            ProcessedActionType.NAVIGATE,
            ProcessedActionType.OPEN_TAB,
        )

        set_button_state(
            self.edit_selector_btn,
            has_action and not is_nav_action and not busy,
            enabled_tip="Edit the Playwright locator/selector for the selected step.",
            disabled_tip=(
                busy_tip
                if busy
                else (
                    "Navigation steps use a URL rather than an element selector."
                    if is_nav_action
                    else "Select a step in the Actions table to edit its selector."
                )
            ),
        )
        set_button_state(
            self.edit_value_btn,
            has_action and not busy,
            enabled_tip="Edit the input value, URL, key, or check state for the selected step.",
            disabled_tip=busy_tip if busy else "Select a step in the Actions table to edit.",
        )
        set_button_state(
            self.move_action_up_btn,
            has_action and action_row > 0 and not busy,
            enabled_tip="Move this step earlier in the test.",
            disabled_tip=(
                busy_tip
                if busy
                else ("Already at the first step." if has_action else "Select a step to reorder.")
            ),
        )
        set_button_state(
            self.move_action_down_btn,
            has_action and defn is not None and action_row < len(defn.actions) - 1 and not busy,
            enabled_tip="Move this step later in the test.",
            disabled_tip=(
                busy_tip
                if busy
                else ("Already at the last step." if has_action else "Select a step to reorder.")
            ),
        )
        set_button_state(
            self.delete_action_btn,
            has_action and not busy,
            enabled_tip="Delete the selected step from this test definition.",
            disabled_tip=busy_tip if busy else "Select a step to delete.",
        )

        assertion_row = self.assertions_table.currentRow()
        has_assertion = defn is not None and 0 <= assertion_row < len(defn.assertions)
        set_button_state(
            self.add_assertion_btn,
            single and not busy,
            enabled_tip="Add a custom Playwright assertion to this test.",
            disabled_tip=busy_tip if busy else "Select a single test definition to add assertions.",
        )
        set_button_state(
            self.toggle_assertion_btn,
            has_assertion and not busy,
            enabled_tip="Enable or disable the selected assertion in generated code.",
            disabled_tip=busy_tip if busy else "Select an assertion row first.",
        )
        set_button_state(
            self.edit_assertion_btn,
            has_assertion and not busy,
            enabled_tip="Edit the expected value or selector for the selected assertion.",
            disabled_tip=busy_tip if busy else "Select an assertion row to edit.",
        )
        set_button_state(
            self.delete_assertion_btn,
            has_assertion and not busy,
            enabled_tip="Remove the selected assertion from this test.",
            disabled_tip=busy_tip if busy else "Select an assertion row to delete.",
        )

        set_button_state(
            self.copy_test_btn,
            single,
            enabled_tip="Copy the selected standalone Pytest function to the clipboard.",
            disabled_tip="Select a single test definition to copy.",
        )
        set_button_state(
            self.copy_scenario_btn,
            defn is not None and defn.scenario_id is not None,
            enabled_tip="Copy all tests belonging to this scenario.",
            disabled_tip="Select an independent scenario-backed test to copy its scenario.",
        )
        set_button_state(
            self.copy_selected_btn,
            bool(selected),
            enabled_tip="Copy all currently highlighted test definitions to the clipboard.",
            disabled_tip="Select one or more test definitions to copy.",
        )
        set_button_state(
            self.copy_all_btn,
            has_included,
            enabled_tip="Copy all included test definitions in this session.",
            disabled_tip="Generate and include at least one test definition first.",
        )
        set_button_state(
            self.copy_fixtures_btn,
            bool(self._definitions),
            enabled_tip="Copy standalone conftest.py fixtures and pytest.ini configuration.",
            disabled_tip="Generate test definitions first to copy fixtures.",
        )
        set_button_state(
            self.export_suite_btn,
            has_included,
            enabled_tip="Export a complete standalone Python + Playwright + Pytest suite folder.",
            disabled_tip="Generate and include at least one test definition to export.",
        )
