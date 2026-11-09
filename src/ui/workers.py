"""Background Qt worker thread for running Playwright Chromium and hybrid recording."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from src.browser import BrowserCallbacks, BrowserLaunchConfig, BrowserManager, PageInfo
from src.execution import ExecutionMode, ExecutionProgressEvent, ExecutionService
from src.projects import SessionService
from src.recording import RawEvent, RecordingEngine
from src.scenarios import ScenarioService
from src.storage import Database, RawEventRepository

logger = logging.getLogger(__name__)

BrowserManagerFactory = Callable[[BrowserCallbacks], BrowserManager]


def _default_browser_manager_factory(callbacks: BrowserCallbacks) -> BrowserManager:
    return BrowserManager(callbacks=callbacks)


class BrowserSessionWorker(QThread):
    """Owns `BrowserManager` and `RecordingEngine` off the Qt UI thread."""

    session_started = Signal(str)
    page_opened = Signal(str, object)
    page_navigated = Signal(str, object)
    page_closed = Signal(str, object)
    events_recorded = Signal(str, int, object)
    session_finished = Signal(str)
    session_failed = Signal(str, str)

    def __init__(
        self,
        project_id: str,
        session_id: str,
        launch_config: BrowserLaunchConfig,
        session_service: SessionService,
        event_repository: RawEventRepository,
        *,
        batch_size: int = 50,
        manager_factory: BrowserManagerFactory | None = None,
        poll_interval_ms: int = 100,
        ui_flush_interval_s: float = 0.25,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_id = project_id
        self._session_id = session_id
        self._launch_config = launch_config
        self._session_service = session_service
        self._event_repository = event_repository
        self._batch_size = batch_size
        self._manager_factory = manager_factory or _default_browser_manager_factory
        self._poll_interval_ms = poll_interval_ms
        self._ui_flush_interval_s = ui_flush_interval_s
        self._stop_event = threading.Event()
        self._pending_ui_events: list[RawEvent] = []
        self._ui_lock = threading.Lock()

    @property
    def session_id(self) -> str:
        """Return the recording session ID associated with this worker."""
        return self._session_id

    def request_stop(self) -> None:
        """Signal the worker loop to close Chromium and exit cleanly."""
        self._stop_event.set()

    def run(self) -> None:
        """Launch Chromium, attach hybrid observers, pump events, and flush on stop."""
        manager_ref: list[BrowserManager] = []

        def resolve_page_id(page: object) -> str | None:
            if not manager_ref:
                return None
            return manager_ref[0].get_page_id(page)  # type: ignore[arg-type]

        engine = RecordingEngine(
            self._event_repository,
            project_id=self._project_id,
            session_id=self._session_id,
            page_id_resolver=resolve_page_id,
            batch_size=self._batch_size,
            on_event=self._queue_ui_event,
        )

        def handle_page_opened(info: PageInfo) -> None:
            # Persist browser_pages row synchronously before any raw_events reference page_id.
            self._session_service.register_page(
                self._session_id,
                page_id=info.page_id,
                tab_index=info.tab_index,
                url=info.url,
                title=info.title,
            )
            engine.on_page_opened(info)
            self.page_opened.emit(self._session_id, info)

        def handle_page_navigated(info: PageInfo) -> None:
            self._session_service.update_page(info.page_id, url=info.url, title=info.title)
            engine.on_page_navigated(info)
            self.page_navigated.emit(self._session_id, info)

        def handle_page_closed(info: PageInfo) -> None:
            self._session_service.close_page(info.page_id)
            engine.on_page_closed(info)
            self.page_closed.emit(self._session_id, info)

        callbacks = BrowserCallbacks(
            on_context_created=engine.attach_context,
            on_page_opened=handle_page_opened,
            on_page_attached=engine.attach_page,
            on_page_navigated=handle_page_navigated,
            on_page_closed=handle_page_closed,
            on_disconnected=self._stop_event.set,
        )
        manager = self._manager_factory(callbacks)
        manager_ref.append(manager)

        last_flush = time.monotonic()
        try:
            manager.launch(self._launch_config)
            self.session_started.emit(self._session_id)
            self._emit_pending_ui_events(engine.event_count)

            while not self._stop_event.is_set():
                still_alive = manager.poll_events(self._poll_interval_ms)
                now = time.monotonic()
                if now - last_flush >= self._ui_flush_interval_s:
                    engine.flush()
                    self._emit_pending_ui_events(engine.event_count)
                    last_flush = now
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
            try:
                engine.flush()
                self._emit_pending_ui_events(engine.event_count)
            except Exception:
                logger.exception("Failed to flush recorded events for session %s", self._session_id)
            finally:
                Database.close_current_thread_connections()

        self.session_finished.emit(self._session_id)

    def _queue_ui_event(self, event: RawEvent) -> None:
        with self._ui_lock:
            self._pending_ui_events.append(event)

    def _emit_pending_ui_events(self, total_count: int) -> None:
        with self._ui_lock:
            if not self._pending_ui_events:
                return
            batch = self._pending_ui_events
            self._pending_ui_events = []
        self.events_recorded.emit(self._session_id, total_count, batch)


class TestExecutionWorker(QThread):
    """Runs Pytest execution via `ExecutionService` off the Qt UI thread."""

    __test__ = False

    progress_event = Signal(object)
    run_completed = Signal(object, object)
    run_failed = Signal(str)

    def __init__(
        self,
        execution_service: ExecutionService,
        *,
        session_id: str,
        definition_ids: list[str] | None = None,
        scenario_id: str | None = None,
        complete_suite: bool = False,
        mode: ExecutionMode = ExecutionMode.INDEPENDENT,
        rerun_failed_run_id: str | None = None,
        headed: bool = False,
        extra_env: dict[str, str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._execution_service = execution_service
        self._session_id = session_id
        self._definition_ids = definition_ids
        self._scenario_id = scenario_id
        self._complete_suite = complete_suite
        self._mode = mode
        self._rerun_failed_run_id = rerun_failed_run_id
        self._headed = headed
        self._extra_env = dict(extra_env) if extra_env else None
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """Signal the managed Pytest subprocess to terminate cleanly."""
        self._cancel_event.set()

    def run(self) -> None:
        """Execute the configured test run and emit live progress and final results."""

        def _on_event(event: ExecutionProgressEvent) -> None:
            self.progress_event.emit(event)

        try:
            if self._rerun_failed_run_id is not None:
                run, results = self._execution_service.rerun_failed(
                    self._rerun_failed_run_id,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
            elif self._scenario_id is not None:
                run, results = self._execution_service.run_scenario(
                    self._session_id,
                    self._scenario_id,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
            elif self._complete_suite:
                run, results = self._execution_service.run_complete_suite(
                    self._session_id,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
            elif self._definition_ids is not None:
                run, results = self._execution_service.execute_definitions(
                    self._session_id,
                    self._definition_ids,
                    self._mode,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
            elif self._mode == ExecutionMode.JOURNEY:
                run, results = self._execution_service.run_journey(
                    self._session_id,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
            else:
                run, results = self._execution_service.run_independent(
                    self._session_id,
                    headed=self._headed,
                    extra_env=self._extra_env,
                    cancel_event=self._cancel_event,
                    on_event=_on_event,
                )
        except Exception as exc:
            logger.exception("Test execution worker failed")
            self.run_failed.emit(str(exc))
            return
        finally:
            Database.close_current_thread_connections()

        self.run_completed.emit(run, results)


class SessionProcessingWorker(QThread):
    """Runs deterministic event processing and scenario detection off the Qt UI thread."""

    processing_finished = Signal(str, object, object)
    processing_failed = Signal(str, str)

    def __init__(
        self,
        scenario_service: ScenarioService,
        session_id: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scenario_service = scenario_service
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        """Return the recording session ID being processed."""
        return self._session_id

    def run(self) -> None:
        """Process raw events into a correlated timeline and scenarios."""
        try:
            result, scenarios = self._scenario_service.process_session(self._session_id)
        except Exception as exc:
            logger.exception("Session processing worker failed for session %s", self._session_id)
            self.processing_failed.emit(self._session_id, str(exc))
            return
        finally:
            Database.close_current_thread_connections()

        self.processing_finished.emit(self._session_id, result, scenarios)
