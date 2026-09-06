"""Application stylesheet for the AutoTest PySide6 desktop shell."""

from __future__ import annotations

APP_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #16181d;
    color: #e6e8ee;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}

QLabel {
    color: #e6e8ee;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 600;
    color: #f3f5f8;
}

QLabel#projectTitle {
    font-size: 20px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#mutedLabel {
    color: #9aa3b2;
}

QLabel#errorLabel {
    color: #f87171;
    font-weight: 500;
}

QLabel#statusBadge {
    padding: 4px 10px;
    border-radius: 10px;
    font-weight: 600;
    font-size: 12px;
    background-color: #272c36;
    color: #cbd5e1;
}

QLabel#statusBadge[state="active"] {
    background-color: #14532d;
    color: #86efac;
}

QLabel#statusBadge[state="launching"] {
    background-color: #1e3a5f;
    color: #93c5fd;
}

QLabel#statusBadge[state="error"] {
    background-color: #7f1d1d;
    color: #fca5a5;
}

QListWidget, QTableWidget, QLineEdit, QTextEdit {
    background-color: #1e2128;
    color: #e6e8ee;
    border: 1px solid #2e3440;
    border-radius: 6px;
    padding: 4px;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #232730;
    color: #cbd5e1;
    border: none;
    border-bottom: 1px solid #2e3440;
    padding: 6px 8px;
    font-weight: 600;
}

QPushButton {
    background-color: #272c36;
    color: #e6e8ee;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #323846;
}

QPushButton:disabled {
    background-color: #1c1f26;
    color: #64748b;
    border-color: #262b35;
}

QPushButton#primaryButton {
    background-color: #2563eb;
    border-color: #1d4ed8;
    color: #ffffff;
}

QPushButton#primaryButton:hover {
    background-color: #1d4ed8;
}

QPushButton#dangerButton {
    background-color: #dc2626;
    border-color: #b91c1c;
    color: #ffffff;
}

QPushButton#dangerButton:hover {
    background-color: #b91c1c;
}

QTabWidget::pane {
    border: 1px solid #2e3440;
    border-radius: 6px;
    background-color: #1a1d24;
}

QTabBar::tab {
    background-color: #1e2128;
    color: #9aa3b2;
    padding: 8px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
}

QTabBar::tab:selected {
    background-color: #272c36;
    color: #ffffff;
    font-weight: 600;
}

QStatusBar {
    background-color: #121418;
    color: #9aa3b2;
    border-top: 1px solid #262b35;
}
"""
