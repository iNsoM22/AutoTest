"""Background Qt worker thread for running the Playwright Chromium session."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from src.browser import BrowserCallbacks, BrowserLaunchConfig, BrowserManager, PageInfo

logger = logging.getLogger(__name__)

BrowserManagerFactory = Callable[[BrowserCallbacks], BrowserManager]


def _default_browser_manager_factory(callbacks: BrowserCallbacks) -> BrowserManager:
    return BrowserManager(callbacks=callbacks)


class BrowserSessionWorker(QThread):
    """Owns a `BrowserManager` on a dedicated thread so the Qt UI stays responsive."""

    session_started = Signal(str)
    page_opened = Signal(str, object)
    page_navigated = Signal(str, object)
    page_closed = Signal(str, object)
    session_finished = Signal(str)
    session_failed = Signal(str, str)

    def __init__(
        self,
        session_id: str,
        launch_config: BrowserLaunchConfig,
        *,
        manager_factory: BrowserManagerFactory | None = None,
        poll_interval_ms: int = 100,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._launch_config = launch_config
        self._manager_factory = manager_factory or _default_browser_manager_factory
        self._poll_interval_ms = poll_interval_ms
        self._stop_event = threading.Event()

    @property
    def session_id(self) -> str:
        """Return the recording session ID associated with this worker."""
        return self._session_id

    def request_stop(self) -> None:
        """Signal the worker loop to close Chromium and exit cleanly."""
        self._stop_event.set()

    def run(self) -> None:
        """Launch Chromium, pump browser events until stopped or closed, then clean up."""
        callbacks = BrowserCallbacks(
            on_page_opened=self._emit_page_opened,
            on_page_navigated=self._emit_page_navigated,
            on_page_closed=self._emit_page_closed,
            on_disconnected=self._stop_event.set,
        )
        manager = self._manager_factory(callbacks)
        try:
            manager.launch(self._launch_config)
            self.session_started.emit(self._session_id)

            while not self._stop_event.is_set():
                still_alive = manager.poll_events(self._poll_interval_ms)
                if not still_alive:
                    break
        except Exception as exc:
            logger.exception("Browser worker failed for session %s", self._session_id)
            self.session_failed.emit(self._session_id, str(exc))
            return
        finally:
            try:
                manager.close()
            except Exception:
                logger.debug("Ignored error during browser worker cleanup", exc_info=True)

        self.session_finished.emit(self._session_id)

    def _emit_page_opened(self, info: PageInfo) -> None:
        self.page_opened.emit(self._session_id, info)

    def _emit_page_navigated(self, info: PageInfo) -> None:
        self.page_navigated.emit(self._session_id, info)

    def _emit_page_closed(self, info: PageInfo) -> None:
        self.page_closed.emit(self._session_id, info)
