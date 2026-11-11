"""Deterministic rule-based scenario detection from processed interaction timelines."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from src.processing.models import OutcomeType, ProcessedActionType, ProcessedEvent
from src.scenarios.models import Scenario
from src.utils import dedupe_preserve, target_label

_USER_ACTION_TYPES = frozenset(
    {
        ProcessedActionType.CLICK,
        ProcessedActionType.FILL,
        ProcessedActionType.SELECT,
        ProcessedActionType.CHECK,
        ProcessedActionType.SUBMIT,
        ProcessedActionType.PRESS_KEY,
        ProcessedActionType.HOVER,
    }
)


def _url_path(url: str) -> str:
    if not url:
        return ""
    return urlsplit(url).path or "/"


def _nav_outcome_path(event: ProcessedEvent) -> str:
    for outcome in event.outcomes:
        if outcome.outcome_type == OutcomeType.NAVIGATION:
            url = str(outcome.details.get("url", ""))
            if url:
                return _url_path(url)
    return ""


def _collect_raw_ids(events: Sequence[ProcessedEvent]) -> list[str]:
    return dedupe_preserve([raw_id for event in events for raw_id in event.raw_event_ids])


def _derive_scenario_name(events: Sequence[ProcessedEvent], index: int) -> str:
    # 1. Prefer a form submission label
    for event in reversed(events):
        if event.action_type == ProcessedActionType.SUBMIT:
            trigger = str(event.payload.get("trigger_label", "")).strip()
            if trigger:
                return f"Submit: {trigger}"
            nav_path = _nav_outcome_path(event)
            if nav_path:
                return f"Submit to {nav_path}"
            return f"Form Submission {index}"

    # 2. Prefer a click that caused navigation or notable DOM change
    for event in reversed(events):
        if event.action_type == ProcessedActionType.CLICK and event.outcomes:
            label = target_label(event.target, default="")
            if label:
                return f"Flow: {label}"

    # 3. Prefer a navigation path
    for event in events:
        if event.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB):
            path = _url_path(str(event.payload.get("url", "")))
            if path and path != "/":
                return f"Visit {path}"
            url = str(event.payload.get("url", "")).strip()
            if url:
                return f"Visit {urlsplit(url).netloc or url}"

    # 4. Fallback to first meaningful user action
    for event in events:
        if event.action_type in _USER_ACTION_TYPES and event.summary:
            return event.summary[:60]

    return f"Scenario {index}"


class ScenarioDetector:
    """Segments a `ProcessedEvent` timeline into logical recorded scenarios.

    Strictly operates on recorded interactions — never invents unperformed scenarios.
    """

    def __init__(self, idle_split_seconds: float = 30.0) -> None:
        self._idle_split_seconds = idle_split_seconds

    def detect(
        self,
        project_id: str,
        session_id: str,
        processed_events: Sequence[ProcessedEvent],
    ) -> list[Scenario]:
        """Group processed events into ordered `Scenario` instances."""
        if not processed_events:
            return []

        groups: list[list[ProcessedEvent]] = []
        current: list[ProcessedEvent] = []
        current_path = ""
        has_user_action = False
        boundary_closed = False

        for event in processed_events:
            if not current:
                current.append(event)
                if event.action_type in (
                    ProcessedActionType.NAVIGATE,
                    ProcessedActionType.OPEN_TAB,
                ):
                    current_path = _url_path(str(event.payload.get("url", "")))
                if event.action_type in _USER_ACTION_TYPES:
                    has_user_action = True
                if self._closes_boundary(event, current_path):
                    boundary_closed = True
                outcome_path = _nav_outcome_path(event)
                if outcome_path:
                    current_path = outcome_path
                continue

            prev = current[-1]
            idle_gap = (event.started_at - prev.ended_at).total_seconds()
            nav_path = (
                _url_path(str(event.payload.get("url", "")))
                if event.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB)
                else ""
            )

            should_start_new = (
                (boundary_closed and event.action_type != ProcessedActionType.CLOSE_TAB)
                or (
                    event.action_type == ProcessedActionType.NAVIGATE
                    and has_user_action
                    and bool(nav_path)
                    and nav_path != current_path
                )
                or (idle_gap >= self._idle_split_seconds and len(current) >= 2 and has_user_action)
            )

            if should_start_new:
                groups.append(current)
                current = [event]
                current_path = nav_path or current_path
                has_user_action = event.action_type in _USER_ACTION_TYPES
                boundary_closed = self._closes_boundary(event, current_path)
            else:
                current.append(event)
                if nav_path:
                    current_path = nav_path
                if event.action_type in _USER_ACTION_TYPES:
                    has_user_action = True
                if self._closes_boundary(event, current_path):
                    boundary_closed = True

            outcome_path = _nav_outcome_path(event)
            if outcome_path:
                current_path = outcome_path

        if current:
            groups.append(current)

        # Build scenarios with deduplicated human-readable names
        scenarios: list[Scenario] = []
        used_names: dict[str, int] = {}
        for idx, group in enumerate(groups, start=1):
            base_name = _derive_scenario_name(group, idx)
            count = used_names.get(base_name, 0) + 1
            used_names[base_name] = count
            final_name = base_name if count == 1 else f"{base_name} ({count})"

            scenarios.append(
                Scenario(
                    project_id=project_id,
                    session_id=session_id,
                    name=final_name,
                    description=f"{len(group)} recorded step(s)",
                    order_index=idx,
                    included=True,
                    processed_event_ids=[e.id for e in group],
                    raw_event_ids=_collect_raw_ids(group),
                )
            )

        return scenarios

    @staticmethod
    def _closes_boundary(event: ProcessedEvent, current_path: str) -> bool:
        if event.action_type == ProcessedActionType.SUBMIT:
            return True
        if event.action_type in (ProcessedActionType.CLICK, ProcessedActionType.PRESS_KEY):
            outcome_path = _nav_outcome_path(event)
            if outcome_path and outcome_path != current_path:
                return True
        return False
