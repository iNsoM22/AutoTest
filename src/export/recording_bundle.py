"""Export and import portable processed recording bundles (JSON)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.processing.models import ProcessedEvent
from src.processing.repository import ProcessedEventRepository
from src.projects.models import RecordingSession, SessionStatus
from src.projects.repository import ProjectRepository, SessionRepository
from src.scenarios.models import Scenario
from src.scenarios.repository import ScenarioRepository


class RecordingBundleError(Exception):
    """Raised when exporting or importing a processed recording bundle fails."""


class ProcessedRecordingBundle(BaseModel):
    """Portable JSON representation of a processed recording session."""

    schema_version: int = 1
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_name: str
    start_url: str = ""
    viewport_width: int = Field(default=1280, ge=320)
    viewport_height: int = Field(default=800, ge=240)
    metadata: dict[str, Any] = Field(default_factory=dict)
    processed_events: list[ProcessedEvent] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)


class RecordingBundleService:
    """Exports processed sessions to JSON bundles and imports them into any project."""

    def __init__(
        self,
        project_repo: ProjectRepository,
        session_repo: SessionRepository,
        processed_event_repo: ProcessedEventRepository,
        scenario_repo: ScenarioRepository,
    ) -> None:
        self._project_repo = project_repo
        self._session_repo = session_repo
        self._processed_repo = processed_event_repo
        self._scenario_repo = scenario_repo

    def export_session(self, session_id: str) -> ProcessedRecordingBundle:
        """Build a portable `ProcessedRecordingBundle` from a processed session."""
        session = self._session_repo.get(session_id)
        if session is None:
            raise RecordingBundleError(f"Recording session not found: {session_id}")
        processed = self._processed_repo.list_by_session(session_id)
        if not processed:
            raise RecordingBundleError(
                "Session has no processed events yet. Process the session before exporting."
            )
        scenarios = self._scenario_repo.list_by_session(session_id)
        viewport_w = int(session.metadata.get("viewport_width", 1280) or 1280)
        viewport_h = int(session.metadata.get("viewport_height", 800) or 800)
        return ProcessedRecordingBundle(
            session_name=session.name,
            start_url=session.start_url,
            viewport_width=max(320, viewport_w),
            viewport_height=max(240, viewport_h),
            metadata=dict(session.metadata),
            processed_events=processed,
            scenarios=scenarios,
        )

    def export_to_file(self, session_id: str, output_path: Path) -> ProcessedRecordingBundle:
        """Export a processed recording session to a JSON file."""
        bundle = self.export_session(session_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return bundle

    def import_bundle(
        self,
        project_id: str,
        bundle: ProcessedRecordingBundle,
        *,
        session_name: str | None = None,
    ) -> RecordingSession:
        """Import a `ProcessedRecordingBundle` into `project_id` as a new completed session."""
        project = self._project_repo.get(project_id)
        if project is None:
            raise RecordingBundleError(f"Project not found: {project_id}")
        if not bundle.processed_events:
            raise RecordingBundleError("Imported bundle contains no processed events.")

        now = datetime.now(UTC)
        resolved_name = (session_name or bundle.session_name or "").strip() or "Imported Recording"
        resolved_url = bundle.start_url.strip() or project.base_url
        merged_meta = dict(bundle.metadata)
        merged_meta.update(
            {
                "imported": True,
                "imported_at": now.isoformat(),
                "viewport_width": bundle.viewport_width,
                "viewport_height": bundle.viewport_height,
            }
        )
        new_session = RecordingSession(
            id=uuid.uuid4().hex,
            project_id=project_id,
            name=resolved_name,
            start_url=resolved_url,
            status=SessionStatus.STOPPED,
            started_at=now,
            ended_at=now,
            created_at=now,
            updated_at=now,
            metadata=merged_meta,
        )

        event_id_map: dict[str, str] = {}
        remapped_events: list[ProcessedEvent] = []
        for idx, ev in enumerate(bundle.processed_events, start=1):
            new_id = uuid.uuid4().hex
            event_id_map[ev.id] = new_id
            remapped_events.append(
                ev.model_copy(
                    update={
                        "id": new_id,
                        "project_id": project_id,
                        "session_id": new_session.id,
                        "order_index": idx,
                    }
                )
            )

        remapped_scenarios: list[Scenario] = []
        for idx, sc in enumerate(bundle.scenarios, start=1):
            mapped_ev_ids = [
                event_id_map[old_id] for old_id in sc.processed_event_ids if old_id in event_id_map
            ]
            remapped_scenarios.append(
                sc.model_copy(
                    update={
                        "id": uuid.uuid4().hex,
                        "project_id": project_id,
                        "session_id": new_session.id,
                        "order_index": idx,
                        "processed_event_ids": mapped_ev_ids,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            )

        with self._scenario_repo.database.transaction():
            created_session = self._session_repo.create(new_session)
            self._processed_repo.replace_for_session(created_session.id, remapped_events)
            self._scenario_repo.replace_for_session(created_session.id, remapped_scenarios)

        return created_session

    def import_from_file(
        self,
        project_id: str,
        input_path: Path,
        *,
        session_name: str | None = None,
    ) -> RecordingSession:
        """Read and validate a JSON bundle from disk and import it into `project_id`."""
        try:
            raw_text = input_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            bundle = ProcessedRecordingBundle.model_validate(payload)
        except Exception as exc:
            raise RecordingBundleError(f"Invalid processed recording bundle: {exc}") from exc
        return self.import_bundle(project_id, bundle, session_name=session_name)
