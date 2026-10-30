"""Application service for processing sessions and editing detected scenarios."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from src.processing.engine import ProcessingEngine
from src.processing.models import ProcessedEvent, ProcessingConfig, ProcessingResult
from src.processing.repository import ProcessedEventRepository
from src.projects.repository import SessionRepository
from src.scenarios.detector import ScenarioDetector
from src.scenarios.models import Scenario, ScenarioError
from src.scenarios.repository import ScenarioRepository
from src.storage.events import RawEventRepository


def _dedupe_preserve(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class ScenarioService:
    """Orchestrates raw-event processing, scenario detection, and review edits.

    Maintains strict separation between immutable `raw_events` and derived
    `processed_events` / `scenarios`.
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        raw_event_repo: RawEventRepository,
        processed_event_repo: ProcessedEventRepository,
        scenario_repo: ScenarioRepository,
        *,
        detector: ScenarioDetector | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._raw_event_repo = raw_event_repo
        self._processed_event_repo = processed_event_repo
        self._scenario_repo = scenario_repo
        self._detector = detector or ScenarioDetector()

    def process_session(
        self,
        session_id: str,
        *,
        config: ProcessingConfig | None = None,
    ) -> tuple[ProcessingResult, list[Scenario]]:
        """Process immutable raw events for a session and regenerate scenarios."""
        session = self._session_repo.get(session_id)
        if session is None:
            raise ScenarioError(f"Recording session '{session_id}' not found.")

        raw_events = self._raw_event_repo.list_by_session(session_id)
        engine = ProcessingEngine(config=config)
        result = engine.process(session_id, raw_events)

        self._processed_event_repo.replace_for_session(session_id, result.processed_events)
        scenarios = self._detector.detect(
            project_id=session.project_id,
            session_id=session_id,
            processed_events=result.processed_events,
        )
        self._scenario_repo.replace_for_session(session_id, scenarios)
        return result, scenarios

    def get_timeline(self, session_id: str) -> list[ProcessedEvent]:
        """Return persisted processed events for a session."""
        return self._processed_event_repo.list_by_session(session_id)

    def list_scenarios(self, session_id: str) -> list[Scenario]:
        """Return persisted scenarios for a session in display order."""
        return self._scenario_repo.list_by_session(session_id)

    def get_scenario_events(self, scenario_id: str) -> list[ProcessedEvent]:
        """Return the ordered `ProcessedEvent` instances belonging to a scenario."""
        scenario = self._require_scenario(scenario_id)
        timeline = {
            e.id: e for e in self._processed_event_repo.list_by_session(scenario.session_id)
        }
        return [timeline[eid] for eid in scenario.processed_event_ids if eid in timeline]

    def rename_scenario(
        self,
        scenario_id: str,
        name: str,
        *,
        description: str | None = None,
    ) -> Scenario:
        """Rename a scenario and optionally update its description."""
        scenario = self._require_scenario(scenario_id)
        cleaned = name.strip()
        if not cleaned:
            raise ScenarioError("Scenario name cannot be empty.")

        updated = scenario.model_copy(
            update={
                "name": cleaned,
                "description": scenario.description if description is None else description.strip(),
                "updated_at": datetime.now(UTC),
            }
        )
        return self._scenario_repo.update(updated)

    def set_scenario_included(self, scenario_id: str, included: bool) -> Scenario:
        """Include or exclude a scenario from downstream test generation."""
        scenario = self._require_scenario(scenario_id)
        updated = scenario.model_copy(
            update={"included": included, "updated_at": datetime.now(UTC)}
        )
        return self._scenario_repo.update(updated)

    def delete_scenario(self, scenario_id: str) -> None:
        """Remove a scenario and re-index remaining scenarios in the session."""
        scenario = self._require_scenario(scenario_id)
        self._scenario_repo.delete(scenario_id)
        self._reindex_session_scenarios(scenario.session_id)

    def merge_scenarios(
        self,
        scenario_ids: Sequence[str],
        *,
        new_name: str | None = None,
    ) -> Scenario:
        """Merge two or more scenarios from the same session in order."""
        unique_ids = _dedupe_preserve(scenario_ids)
        if len(unique_ids) < 2:
            raise ScenarioError("Select at least two scenarios to merge.")

        targets = [self._require_scenario(sid) for sid in unique_ids]
        session_ids = {s.session_id for s in targets}
        if len(session_ids) != 1:
            raise ScenarioError("Can only merge scenarios from the same recording session.")

        targets.sort(key=lambda s: s.order_index)
        order_indices = [s.order_index for s in targets]
        expected_indices = list(range(order_indices[0], order_indices[0] + len(order_indices)))
        if order_indices != expected_indices:
            raise ScenarioError("Can only merge adjacent scenarios in chronological order.")

        first = targets[0]
        merged_processed_ids = _dedupe_preserve(
            [eid for s in targets for eid in s.processed_event_ids]
        )
        merged_raw_ids = _dedupe_preserve([rid for s in targets for rid in s.raw_event_ids])
        merged_name = (new_name.strip() if new_name else "") or first.name

        merged = first.model_copy(
            update={
                "name": merged_name,
                "description": f"{len(merged_processed_ids)} recorded step(s)",
                "included": any(s.included for s in targets),
                "processed_event_ids": merged_processed_ids,
                "raw_event_ids": merged_raw_ids,
                "updated_at": datetime.now(UTC),
            }
        )
        self._scenario_repo.update(merged)
        for extra in targets[1:]:
            self._scenario_repo.delete(extra.id)
        self._reindex_session_scenarios(first.session_id)
        return self._require_scenario(merged.id)

    def split_scenario(
        self,
        scenario_id: str,
        split_at_index: int,
        *,
        second_name: str | None = None,
    ) -> tuple[Scenario, Scenario]:
        """Split a scenario into two consecutive scenarios at `split_at_index`."""
        scenario = self._require_scenario(scenario_id)
        step_ids = scenario.processed_event_ids
        if split_at_index < 1 or split_at_index >= len(step_ids):
            raise ScenarioError(f"Split index must be between 1 and {max(len(step_ids) - 1, 1)}.")

        timeline = {
            e.id: e for e in self._processed_event_repo.list_by_session(scenario.session_id)
        }
        left_ids = step_ids[:split_at_index]
        right_ids = step_ids[split_at_index:]

        left_raw = _dedupe_preserve(
            [rid for eid in left_ids if eid in timeline for rid in timeline[eid].raw_event_ids]
        )
        right_raw = _dedupe_preserve(
            [rid for eid in right_ids if eid in timeline for rid in timeline[eid].raw_event_ids]
        )

        now = datetime.now(UTC)
        left_updated = scenario.model_copy(
            update={
                "description": f"{len(left_ids)} recorded step(s)",
                "processed_event_ids": left_ids,
                "raw_event_ids": left_raw,
                "updated_at": now,
            }
        )
        right_name = (second_name.strip() if second_name else "") or f"{scenario.name} (Part 2)"
        right_created = Scenario(
            project_id=scenario.project_id,
            session_id=scenario.session_id,
            name=right_name,
            description=f"{len(right_ids)} recorded step(s)",
            order_index=scenario.order_index + 1,
            included=scenario.included,
            processed_event_ids=right_ids,
            raw_event_ids=right_raw,
            created_at=now,
            updated_at=now,
        )

        existing = self._scenario_repo.list_by_session(scenario.session_id)
        reordered: list[Scenario] = []
        for item in existing:
            if item.id == scenario.id:
                reordered.append(left_updated)
                reordered.append(right_created)
            else:
                reordered.append(item)

        normalized = [
            s.model_copy(update={"order_index": idx}) for idx, s in enumerate(reordered, start=1)
        ]
        self._scenario_repo.replace_for_session(scenario.session_id, normalized)
        return self._require_scenario(left_updated.id), self._require_scenario(right_created.id)

    def reorder_scenarios(
        self,
        session_id: str,
        ordered_scenario_ids: Sequence[str],
    ) -> list[Scenario]:
        """Persist a new display/execution order for a session's scenarios."""
        existing = self._scenario_repo.list_by_session(session_id)
        by_id = {s.id: s for s in existing}
        if set(ordered_scenario_ids) != set(by_id.keys()) or len(ordered_scenario_ids) != len(
            existing
        ):
            raise ScenarioError("Ordered scenario IDs must match the session's scenarios exactly.")

        now = datetime.now(UTC)
        reordered = [
            by_id[sid].model_copy(update={"order_index": idx, "updated_at": now})
            for idx, sid in enumerate(ordered_scenario_ids, start=1)
        ]
        return self._scenario_repo.replace_for_session(session_id, reordered)

    def _require_scenario(self, scenario_id: str) -> Scenario:
        scenario = self._scenario_repo.get(scenario_id)
        if scenario is None:
            raise ScenarioError(f"Scenario '{scenario_id}' not found.")
        return scenario

    def _reindex_session_scenarios(self, session_id: str) -> None:
        remaining = self._scenario_repo.list_by_session(session_id)
        normalized = [
            s.model_copy(update={"order_index": idx}) for idx, s in enumerate(remaining, start=1)
        ]
        self._scenario_repo.replace_for_session(session_id, normalized)
