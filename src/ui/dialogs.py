"""Modal dialogs for creating and editing AutoTest projects."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.projects import Project


class ProjectDialog(QDialog):
    """Dialog for creating a new project or editing an existing one."""

    def __init__(
        self,
        *,
        project: Project | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Project" if project is not None else "New Project")
        self.setMinimumWidth(440)

        self._name_input = QLineEdit(self)
        self._name_input.setPlaceholderText("e.g. Checkout Web App")

        self._base_url_input = QLineEdit(self)
        self._base_url_input.setPlaceholderText("https://example.com")

        self._description_input = QTextEdit(self)
        self._description_input.setPlaceholderText("Optional description of the target application")
        self._description_input.setFixedHeight(84)

        self._error_label = QLabel(self)
        self._error_label.setObjectName("errorLabel")
        self._error_label.hide()

        if project is not None:
            self._name_input.setText(project.name)
            self._base_url_input.setText(project.base_url)
            self._description_input.setPlainText(project.description)

        form = QFormLayout()
        form.addRow("Project Name*", self._name_input)
        form.addRow("Base URL", self._base_url_input)
        form.addRow("Description", self._description_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._handle_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(self._error_label)
        layout.addWidget(buttons)

    def _handle_accept(self) -> None:
        if not self._name_input.text().strip():
            self._error_label.setText("Project name is required.")
            self._error_label.show()
            self._name_input.setFocus()
            return
        self._error_label.hide()
        self.accept()

    def get_values(self) -> tuple[str, str, str]:
        """Return `(name, base_url, description)` entered by the user."""
        return (
            self._name_input.text().strip(),
            self._base_url_input.text().strip(),
            self._description_input.toPlainText().strip(),
        )
