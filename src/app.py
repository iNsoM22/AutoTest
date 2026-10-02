"""Application bootstrap, service wiring, and desktop entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from src import __version__
from src.config import AppSettings, configure_logging, get_settings
from src.definitions import TestDefinitionRepository, TestDefinitionService
from src.export import CopyExportService
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
    session_service.recover_interrupted_sessions()

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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI / desktop entry point."""
    args = _parse_args(argv)
    context = create_app_context()
    try:
        if args.check:
            logger.info("Health check succeeded (db=%s).", context.settings.db_path)
            return 0

        from PySide6.QtWidgets import QApplication

        from src.ui import APP_STYLESHEET, MainWindow

        app = QApplication(sys.argv if argv is None else ["autotest", *argv])
        app.setApplicationName("AutoTest")
        app.setApplicationVersion(__version__)
        app.setStyleSheet(APP_STYLESHEET)

        window = MainWindow(
            context.settings,
            context.project_service,
            context.session_service,
            context.event_repository,
            scenario_service=context.scenario_service,
            definition_service=context.definition_service,
            copy_service=context.copy_service,
        )
        window.show()
        return int(app.exec())
    finally:
        context.close()
