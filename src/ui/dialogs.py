"""Modal dialogs for creating and editing AutoTest projects."""

from __future__ import annotations

from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.projects import Project
from src.ui.theme import make_icon, set_button_state


def _is_valid_optional_url(url: str) -> bool:
    """Return True when `url` is blank or has a valid http/https/file scheme."""
    if not url:
        return True
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https", "file") and bool(parsed.netloc or parsed.path)


class ProjectDialog(QDialog):
    """Dialog for creating a new project or editing an existing one."""

    def __init__(
        self,
        *,
        project: Project | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = project is not None
        self.setWindowTitle("Edit Project" if self._is_edit else "New Project")
        self.setWindowIcon(make_icon("brand", size=24))
        self.setMinimumWidth(480)

        header = QLabel(
            "Update project details and default target URL."
            if self._is_edit
            else "Create a workspace to organize recording sessions, scenarios, and Pytest suites.",
            self,
        )
        header.setObjectName("mutedLabel")
        header.setWordWrap(True)

        self._name_input = QLineEdit(self)
        self._name_input.setPlaceholderText("e.g. Checkout Web App")
        self._name_input.setToolTip("Required. A short, recognizable name for the application.")

        self._base_url_input = QLineEdit(self)
        self._base_url_input.setPlaceholderText("https://example.com (optional)")
        self._base_url_input.setToolTip(
            "Optional default URL opened when starting a recording session (http://, https://, or file://)."
        )

        self._description_input = QTextEdit(self)
        self._description_input.setPlaceholderText(
            "Optional notes about the target environment, test accounts, or scope"
        )
        self._description_input.setToolTip("Optional notes shown in the project header.")
        self._description_input.setFixedHeight(88)

        self._error_label = QLabel(self)
        self._error_label.setObjectName("errorLabel")
        self._error_label.setWordWrap(True)
        self._error_label.hide()

        if project is not None:
            self._name_input.setText(project.name)
            self._base_url_input.setText(project.base_url)
            self._description_input.setPlainText(project.description)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Project Name*", self._name_input)
        form.addRow("Base URL", self._base_url_input)
        form.addRow("Description", self._description_input)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._ok_btn: QPushButton | None = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok_btn is not None:
            self._ok_btn.setText("Save Changes" if self._is_edit else "Create Project")
            self._ok_btn.setObjectName("primaryButton")
        self._buttons.accepted.connect(self._handle_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(header)
        layout.addLayout(form)
        layout.addWidget(self._error_label)
        layout.addWidget(self._buttons)

        self._name_input.textChanged.connect(self._validate_live)
        self._base_url_input.textChanged.connect(self._validate_live)
        self._validate_live()

    def _validate_live(self) -> None:
        name = self._name_input.text().strip()
        url = self._base_url_input.text().strip()
        error = ""
        if not name:
            error = "Enter a project name to continue."
        elif not _is_valid_optional_url(url):
            error = "Base URL must start with http://, https://, or file://."

        if url and not _is_valid_optional_url(url):
            self._error_label.setText(error)
            self._error_label.show()
        else:
            self._error_label.hide()

        if self._ok_btn is not None:
            action = "Save project changes" if self._is_edit else "Create this project"
            set_button_state(
                self._ok_btn,
                not error,
                enabled_tip=action,
                disabled_tip=error or action,
            )

    def _handle_accept(self) -> None:
        name = self._name_input.text().strip()
        url = self._base_url_input.text().strip()
        if not name:
            self._error_label.setText("Project name is required.")
            self._error_label.show()
            self._name_input.setFocus()
            return
        if not _is_valid_optional_url(url):
            self._error_label.setText("Base URL must start with http://, https://, or file://.")
            self._error_label.show()
            self._base_url_input.setFocus()
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
