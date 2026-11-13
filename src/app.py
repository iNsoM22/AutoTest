"""Application bootstrap, service wiring, and desktop entry point."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from dataclasses import dataclass

from src import __version__
from src.config import (
    AppSettings,
    configure_logging,
    get_settings,
    run_environment_diagnostics,
)
from src.definitions import TestDefinitionRepository, TestDefinitionService
from src.execution import ExecutionRepository, ExecutionService
from src.export import CopyExportService, RecordingBundleService
from src.processing import ProcessedEventRepository
from src.projects import ProjectRepository, ProjectService, SessionRepository, SessionService
from src.scenarios import ScenarioRepository, ScenarioService
from src.storage import ArtifactManager, ArtifactRepository, Database, RawEventRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContext:
    """Initialized core services for an AutoTest runtime instance."""

    settings: AppSettings
    database: Database
    artifacts: ArtifactManager
    artifact_repository: ArtifactRepository
    event_repository: RawEventRepository
    processed_event_repository: ProcessedEventRepository
    scenario_service: ScenarioService
    definition_service: TestDefinitionService
    copy_service: CopyExportService
    recording_bundle_service: RecordingBundleService
    execution_service: ExecutionService
    project_service: ProjectService
    session_service: SessionService

    def close(self) -> None:
        """Release database connections and runtime resources."""
        self.database.close_all()


def bootstrap(settings: AppSettings | None = None) -> AppSettings:
    """Initialize application directories, configuration, and logging."""
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    configure_logging(active_settings)
    logger.info(
        "AutoTest v%s initialized (env=%s, data_dir=%s)",
        __version__,
        active_settings.env,
        active_settings.resolved_data_dir,
    )
    return active_settings


def create_app_context(settings: AppSettings | None = None) -> AppContext:
    """Bootstrap configuration, initialize SQLite & migrations, and build services."""
    active_settings = bootstrap(settings)
    database = Database(active_settings.db_path, wal_mode=active_settings.sqlite_wal_mode)
    database.initialize()

    artifacts = ArtifactManager(active_settings.artifacts_dir)
    artifacts.ensure_base_dir()
    artifact_repo = ArtifactRepository(database)
    event_repo = RawEventRepository(database)
    processed_event_repo = ProcessedEventRepository(database)
    scenario_repo = ScenarioRepository(database)
    definition_repo = TestDefinitionRepository(database)

    project_repo = ProjectRepository(database)
    session_repo = SessionRepository(database)
    project_service = ProjectService(project_repo, artifacts)
    session_service = SessionService(session_repo, project_repo, artifacts)
    scenario_service = ScenarioService(
        session_repo,
        event_repo,
        processed_event_repo,
        scenario_repo,
    )
    definition_service = TestDefinitionService(
        project_repo,
        session_repo,
        processed_event_repo,
        scenario_repo,
        definition_repo,
    )
    copy_service = CopyExportService()
    recording_bundle_service = RecordingBundleService(
        project_repo,
        session_repo,
        processed_event_repo,
        scenario_repo,
    )
    execution_repo = ExecutionRepository(database)
    execution_service = ExecutionService(
        project_repo,
        session_repo,
        definition_repo,
        execution_repo,
        artifacts,
        artifact_repo,
        copy_service=copy_service,
    )
    session_service.recover_interrupted_sessions()
    execution_service.recover_interrupted_runs()

    return AppContext(
        settings=active_settings,
        database=database,
        artifacts=artifacts,
        artifact_repository=artifact_repo,
        event_repository=event_repo,
        processed_event_repository=processed_event_repo,
        scenario_service=scenario_service,
        definition_service=definition_service,
        copy_service=copy_service,
        recording_bundle_service=recording_bundle_service,
        execution_service=execution_service,
        project_service=project_service,
        session_service=session_service,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="autotest", description="AutoTest E2E Desktop Recorder")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Initialize configuration, SQLite database, and migrations, then exit without UI.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Verify runtime dependencies (Python, SQLite, PySide6, Pytest, Playwright Chromium).",
    )
    return parser.parse_args(argv)


def _attach_parent_console_if_frozen() -> None:
    """Attach to parent console on Windows when running CLI flags from a GUI-frozen binary."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):
            if sys.stdout is None:
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
            if sys.stderr is None:
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    except Exception:
        pass


def _run_embedded_pytest(pytest_args: list[str]) -> int:
    """Execute Pytest directly inside a frozen executable subprocess."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8", errors="replace")
    extra_pypath = os.environ.get("PYTHONPATH", "")
    for entry in reversed(extra_pypath.split(os.pathsep)):
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)
    import pytest

    return int(pytest.main(pytest_args))


def main(argv: list[str] | None = None) -> int:
    """CLI / desktop entry point."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "--run-pytest":
        return _run_embedded_pytest(raw_argv[1:])

    if raw_argv:
        _attach_parent_console_if_frozen()

    args = _parse_args(raw_argv)
    if args.doctor:
        settings = bootstrap()
        report = run_environment_diagnostics(settings)
        print(report.format_text())
        return 0 if report.ok else 1

    context = create_app_context()
    try:
        if args.check:
            logger.info("Health check succeeded (db=%s).", context.settings.db_path)
            return 0

        from PySide6.QtWidgets import QApplication

        from src.ui import APP_STYLESHEET, MainWindow, make_icon

        app = QApplication(sys.argv if argv is None else ["autotest", *argv])
        app.setApplicationName("AutoTest")
        app.setApplicationVersion(__version__)
        app.setWindowIcon(make_icon("brand", size=24))
        app.setStyleSheet(APP_STYLESHEET)

        window = MainWindow(
            context.settings,
            context.project_service,
            context.session_service,
            context.event_repository,
            scenario_service=context.scenario_service,
            definition_service=context.definition_service,
            copy_service=context.copy_service,
            execution_service=context.execution_service,
            artifacts=context.artifacts,
            artifact_repository=context.artifact_repository,
            recording_bundle_service=context.recording_bundle_service,
        )
        window.show()
        return int(app.exec())
    finally:
        context.close()


if __name__ == "__main__":
    raise SystemExit(main())
