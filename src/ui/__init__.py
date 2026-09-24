"""PySide6 desktop UI shell, dialogs, and background workers."""

from src.ui.dialogs import ProjectDialog
from src.ui.main_window import MainWindow
from src.ui.review_widget import ReviewWidget
from src.ui.styles import APP_STYLESHEET
from src.ui.workers import BrowserManagerFactory, BrowserSessionWorker

__all__ = [
    "APP_STYLESHEET",
    "BrowserManagerFactory",
    "BrowserSessionWorker",
    "MainWindow",
    "ProjectDialog",
    "ReviewWidget",
]
