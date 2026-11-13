"""Complete application stylesheet for the AutoTest PySide6 desktop shell.

All colors, typography, and component states derive from `src.ui.theme.TOKENS`
so there are zero unstyled Qt defaults, zero theme gaps across dialogs/scrollbars/
tooltips/inputs, and crisp visual hierarchy across every surface.
"""

from __future__ import annotations

from src.ui.theme import TOKENS as T

APP_STYLESHEET = f"""
/* === Global Base === */
QWidget {{
    color: {T.text_primary};
    font-family: {T.font_sans};
    font-size: 13px;
    selection-background-color: {T.accent_subtle};
    selection-color: {T.accent_fg};
}}

QMainWindow, QDialog, QMessageBox, QInputDialog {{
    background-color: {T.canvas};
}}

QWidget#sidebarPanel {{
    background-color: {T.surface};
    border-right: 1px solid {T.border_subtle};
}}

QWidget#workspacePanel {{
    background-color: {T.canvas};
}}

QFrame#surfaceCard {{
    background-color: {T.surface};
    border: 1px solid {T.border_subtle};
    border-radius: 8px;
}}

QFrame#toolbarStrip {{
    background-color: {T.surface};
    border: 1px solid {T.border_subtle};
    border-radius: 7px;
}}

QFrame#hairlineDivider {{
    background-color: {T.border_subtle};
    border: none;
    max-height: 1px;
}}

QFrame#toolbarSeparator {{
    background-color: {T.border_default};
    border: none;
    max-width: 1px;
}}

/* === Typography === */
QLabel {{
    background: transparent;
    color: {T.text_primary};
}}

QLabel#brandTitle {{
    font-size: 15px;
    font-weight: 700;
    color: {T.text_primary};
}}

QLabel#brandVersion {{
    font-size: 11px;
    font-weight: 600;
    color: {T.accent_fg};
    background-color: {T.accent_subtle};
    border: 1px solid {T.accent_border};
    border-radius: 9px;
    padding: 1px 7px;
}}

QLabel#projectTitle {{
    font-size: 19px;
    font-weight: 700;
    color: {T.text_primary};
}}

QLabel#sectionTitle {{
    font-size: 13.5px;
    font-weight: 600;
    color: {T.text_primary};
}}

QLabel#mutedLabel {{
    color: {T.text_secondary};
    font-size: 12.5px;
}}

QLabel#hintBanner {{
    color: {T.text_secondary};
    background-color: {T.surface_alt};
    border: 1px solid {T.border_subtle};
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 12px;
}}

QLabel#errorLabel {{
    color: {T.danger_fg};
    background-color: {T.danger_bg};
    border: 1px solid {T.danger_border};
    border-radius: 6px;
    padding: 6px 10px;
    font-weight: 500;
    font-size: 12.5px;
}}

QLabel#successInline {{
    color: {T.success_fg};
    font-size: 12px;
    font-weight: 500;
}}

/* === Status Badge === */
QLabel#statusBadge {{
    padding: 4px 12px;
    border-radius: 12px;
    font-weight: 600;
    font-size: 12px;
    background-color: {T.surface_elevated};
    color: {T.text_secondary};
    border: 1px solid {T.border_default};
}}

QLabel#statusBadge[state="active"] {{
    background-color: {T.success_bg};
    color: {T.success_fg};
    border-color: {T.success_border};
}}

QLabel#statusBadge[state="launching"] {{
    background-color: {T.info_bg};
    color: {T.info_fg};
    border-color: {T.info_border};
}}

QLabel#statusBadge[state="error"] {{
    background-color: {T.danger_bg};
    color: {T.danger_fg};
    border-color: {T.danger_border};
}}

/* === Workflow Stepper Pills === */
QPushButton#stepPill {{
    background-color: transparent;
    color: {T.text_secondary};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 500;
}}

QPushButton#stepPill:hover {{
    background-color: {T.surface_hover};
    color: {T.text_primary};
}}

QPushButton#stepPill[active="true"] {{
    background-color: {T.accent_subtle};
    color: {T.accent_fg};
    border: 1px solid {T.accent_border};
    font-weight: 600;
}}

QPushButton#stepPill:disabled {{
    background-color: transparent;
    color: {T.text_disabled};
    border-color: transparent;
}}

QLabel#stepArrow {{
    color: {T.text_muted};
    font-size: 11px;
    padding: 0 2px;
}}

/* === Buttons === */
QPushButton {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_default};
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 500;
    min-height: 18px;
}}

QPushButton:hover {{
    background-color: {T.surface_hover};
    border-color: {T.border_strong};
}}

QPushButton:pressed {{
    background-color: {T.surface_pressed};
    border-color: {T.accent_border};
}}

QPushButton:focus {{
    border-color: {T.accent};
    outline: none;
}}

QPushButton:checked {{
    background-color: {T.accent_subtle};
    color: {T.accent_fg};
    border-color: {T.accent};
}}

QPushButton:disabled {{
    background-color: {T.surface};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

QPushButton#primaryButton {{
    background-color: {T.accent};
    border-color: {T.accent};
    color: {T.accent_fg};
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background-color: {T.accent_hover};
    border-color: {T.accent_hover};
}}

QPushButton#primaryButton:pressed {{
    background-color: {T.accent_pressed};
    border-color: {T.accent_pressed};
}}

QPushButton#primaryButton:disabled {{
    background-color: {T.surface_elevated};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

QPushButton#dangerButton {{
    background-color: {T.danger};
    border-color: {T.danger};
    color: #FFFFFF;
    font-weight: 600;
}}

QPushButton#dangerButton:hover {{
    background-color: {T.danger_hover};
    border-color: {T.danger_hover};
}}

QPushButton#dangerButton:pressed {{
    background-color: {T.danger_pressed};
    border-color: {T.danger_pressed};
}}

QPushButton#dangerButton:disabled {{
    background-color: {T.surface_elevated};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

QPushButton#subtleDangerButton {{
    background-color: transparent;
    color: {T.danger_fg};
    border: 1px solid {T.border_default};
}}

QPushButton#subtleDangerButton:hover {{
    background-color: {T.danger_bg};
    border-color: {T.danger_border};
}}

QPushButton#subtleDangerButton:disabled {{
    background-color: {T.surface};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

QPushButton#ghostButton {{
    background-color: transparent;
    color: {T.text_secondary};
    border: 1px solid transparent;
    padding: 5px 9px;
}}

QPushButton#ghostButton:hover {{
    background-color: {T.surface_hover};
    color: {T.text_primary};
    border-color: {T.border_subtle};
}}

QPushButton#ghostButton:disabled {{
    color: {T.text_disabled};
}}

QPushButton#iconButton {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_default};
    border-radius: 6px;
    padding: 5px;
    min-width: 28px;
    max-width: 34px;
}}

QPushButton#iconButton:hover {{
    background-color: {T.surface_hover};
    border-color: {T.border_strong};
}}

QPushButton#iconButton:disabled {{
    background-color: {T.surface};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

/* === Inputs === */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_default};
    border-radius: 6px;
    padding: 6px 9px;
}}

QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: {T.border_strong};
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {T.accent};
    background-color: {T.surface_alt};
}}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QComboBox:disabled {{
    background-color: {T.surface};
    color: {T.text_disabled};
    border-color: {T.border_subtle};
}}

QPlainTextEdit#codePreview {{
    background-color: {T.surface_code};
    color: {T.text_primary};
    border: 1px solid {T.border_subtle};
    border-radius: 6px;
    font-family: {T.font_mono};
    font-size: 12px;
    padding: 8px;
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_strong};
    selection-background-color: {T.accent_subtle};
    selection-color: {T.accent_fg};
    outline: none;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {T.surface_hover};
    border: none;
    width: 18px;
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {T.surface_pressed};
}}

/* === CheckBox === */
QCheckBox {{
    color: {T.text_primary};
    spacing: 7px;
}}

QCheckBox:disabled {{
    color: {T.text_disabled};
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {T.border_strong};
    background-color: {T.surface_elevated};
}}

QCheckBox::indicator:hover {{
    border-color: {T.accent};
}}

QCheckBox::indicator:checked {{
    background-color: {T.accent};
    border-color: {T.accent};
}}

QCheckBox::indicator:disabled {{
    background-color: {T.surface};
    border-color: {T.border_subtle};
}}

/* === Lists & Tables === */
QListWidget, QTableWidget {{
    background-color: {T.surface};
    alternate-background-color: {T.surface_alt};
    color: {T.text_primary};
    border: 1px solid {T.border_subtle};
    border-radius: 7px;
    gridline-color: {T.border_subtle};
    outline: none;
}}

QListWidget {{
    padding: 4px;
}}

QListWidget::item {{
    padding: 8px 10px;
    border-radius: 5px;
    margin: 1px 0;
}}

QListWidget::item:hover {{
    background-color: {T.surface_hover};
}}

QListWidget::item:selected {{
    background-color: {T.accent_subtle};
    color: {T.accent_fg};
    border: 1px solid {T.accent_border};
}}

QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}

QTableWidget::item:hover {{
    background-color: {T.surface_hover};
}}

QTableWidget::item:selected {{
    background-color: {T.accent_subtle};
    color: {T.accent_fg};
}}

QHeaderView::section {{
    background-color: {T.surface_elevated};
    color: {T.text_secondary};
    border: none;
    border-bottom: 1px solid {T.border_default};
    border-right: 1px solid {T.border_subtle};
    padding: 7px 9px;
    font-weight: 600;
    font-size: 12px;
}}

QHeaderView::section:last {{
    border-right: none;
}}

QTableCornerButton::section {{
    background-color: {T.surface_elevated};
    border: none;
    border-bottom: 1px solid {T.border_default};
}}

/* === Tabs === */
QTabWidget::pane {{
    border: 1px solid {T.border_subtle};
    border-radius: 7px;
    background-color: {T.surface};
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {T.text_secondary};
    padding: 8px 14px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-weight: 500;
}}

QTabBar::tab:hover {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
}}

QTabBar::tab:selected {{
    background-color: {T.surface};
    color: {T.text_primary};
    border-color: {T.border_subtle};
    border-top: 2px solid {T.accent};
    font-weight: 600;
}}

QTabBar::tab:disabled {{
    background-color: transparent;
    color: {T.text_disabled};
    border-color: transparent;
}}

/* === Splitters === */
QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 6px;
}}

QSplitter::handle:vertical {{
    height: 6px;
}}

QSplitter::handle:hover {{
    background-color: {T.accent_border};
    border-radius: 2px;
}}

/* === Scrollbars === */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {T.border_default};
    min-height: 28px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: {T.border_strong};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {T.border_default};
    min-width: 28px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {T.border_strong};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
    width: 0px;
}}

/* === Progress Bar === */
QProgressBar {{
    background-color: {T.surface_elevated};
    border: none;
    border-radius: 2px;
    max-height: 4px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {T.accent};
    border-radius: 2px;
}}

/* === Tooltips === */
QToolTip {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_strong};
    border-radius: 6px;
    padding: 6px 9px;
    font-size: 12px;
}}

/* === Status Bar === */
QStatusBar {{
    background-color: {T.surface};
    color: {T.text_secondary};
    border-top: 1px solid {T.border_subtle};
    font-size: 12px;
    padding: 2px 8px;
}}

QStatusBar::item {{
    border: none;
}}

/* === Menus === */
QMenu {{
    background-color: {T.surface_elevated};
    color: {T.text_primary};
    border: 1px solid {T.border_strong};
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 14px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {T.accent_subtle};
    color: {T.accent_fg};
}}
"""
