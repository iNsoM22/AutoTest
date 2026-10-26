"""Application service for generating and editing Layer 3 test definitions."""

from __future__ import annotations

from datetime import UTC, datetime

from src.definitions.builder import TestDefinitionBuilder
from src.definitions.models import (
    AssertionType,
    SelectorCandidate,
    TestAssertion,
    TestDefinition,
    TestDefinitionError,
    TestExecutionSettings,
    slugify_test_function_name,
)
from src.definitions.repository import TestDefinitionRepository
from src.processing.repository import ProcessedEventRepository
from src.projects.repository import ProjectRepository, SessionRepository
from src.scenarios.repository import ScenarioRepository


class TestDefinitionService:
    """Manages generation and user editing of Layer 3 `TestDefinition`s.

    Never mutates Layer 1 (`raw_events`) or Layer 2 (`processed_events`, `scenarios`).
    """

    __test__ = False

    def __init__(
        self,
        project_repo: ProjectRepository,
        session_repo: SessionRepository,
        processed_event_repo: ProcessedEventRepository,
        scenario_repo: ScenarioRepository,
        definition_repo: TestDefinitionRepository,
        *,
        builder: TestDefinitionBuilder | None = None,
    ) -> None:
        self._project_repo = project_repo
        self._session_repo = session_repo
        self._processed_event_repo = processed_event_repo
        self._scenario_repo = scenario_repo
        self._definition_repo = definition_repo
        self._builder = builder or TestDefinitionBuilder()

    def generate_from_session(
        self,
        session_id: str,
        *,
        include_journey: bool = True,
    ) -> list[TestDefinition]:
        """Generate and persist editable test definitions from a session's included scenarios."""
        session = self._session_repo.get(session_id)
        if session is None:
            raise TestDefinitionError(f"Recording session '{session_id}' not found.")

        project = self._project_repo.get(session.project_id)
        scenarios = self._scenario_repo.list_by_session(session_id)
        if not scenarios:
            raise TestDefinitionError(
                "No scenarios found for this session. Process the session first."
            )

        included = [s for s in scenarios if s.included]
        if not included:
            raise TestDefinitionError(
                "All scenarios in this session are excluded. Include at least one scenario."
            )

        timeline = self._processed_event_repo.list_by_session(session_id)
        definitions = self._builder.build(
            project_id=session.project_id,
            session_id=session_id,
            scenarios=scenarios,
            timeline=timeline,
            base_url=project.base_url if project is not None else "",
            session_start_url=session.start_url,
            include_journey=include_journey,
        )
        if not definitions:
            raise TestDefinitionError(
                "Included scenarios contain no processed steps to generate tests from."
            )
        return self._definition_repo.replace_for_session(session_id, definitions)

    def list_by_session(self, session_id: str) -> list[TestDefinition]:
        """Return all persisted test definitions for a recording session."""
        return self._definition_repo.list_by_session(session_id)

    def get(self, definition_id: str) -> TestDefinition:
        """Fetch a single test definition or raise `TestDefinitionError`."""
        return self._require_definition(definition_id)

    def update_metadata(
        self,
        definition_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        included: bool | None = None,
        settings: TestExecutionSettings | None = None,
    ) -> TestDefinition:
        """Update test name, description, inclusion flag, or execution settings."""
        defn = self._require_definition(definition_id)
        updates: dict[str, object] = {"updated_at": datetime.now(UTC)}

        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise TestDefinitionError("Test definition name cannot be empty.")
            updates["name"] = cleaned
            updates["function_name"] = self._unique_function_name(
                defn.session_id,
                cleaned,
                exclude_id=defn.id,
            )
        if description is not None:
            updates["description"] = description.strip()
        if included is not None:
            updates["included"] = included
        if settings is not None:
            updates["settings"] = settings

        updated = defn.model_copy(update=updates)
        return self._definition_repo.update(updated)

    def update_action(
        self,
        definition_id: str,
        action_id: str,
        *,
        summary: str | None = None,
        selector: SelectorCandidate | None = None,
        value: str | None = None,
        url: str | None = None,
        key: str | None = None,
    ) -> TestDefinition:
        """Edit an action's summary, selector, value, URL, or key."""
        defn = self._require_definition(definition_id)
        new_actions = []
        found = False
        for action in defn.actions:
            if action.id == action_id:
                found = True
                patch: dict[str, object] = {}
                if summary is not None and summary.strip():
                    patch["summary"] = summary.strip()
                if selector is not None:
                    patch["selector"] = selector
                if value is not None:
                    patch["value"] = value
                    patch["is_redacted"] = False
                if url is not None:
                    patch["url"] = url.strip()
                if key is not None:
                    patch["key"] = key.strip()
                new_actions.append(action.model_copy(update=patch))
            else:
                new_actions.append(action)

        if not found:
            raise TestDefinitionError(f"Action '{action_id}' not found in test '{defn.name}'.")

        updated = defn.model_copy(update={"actions": new_actions, "updated_at": datetime.now(UTC)})
        return self._definition_repo.update(updated)

    def move_action(self, definition_id: str, action_id: str, delta: int) -> TestDefinition:
        """Move an action up (`delta=-1`) or down (`delta=1`) within a test definition."""
        defn = self._require_definition(definition_id)
        actions = list(defn.actions)
        idx = next((i for i, a in enumerate(actions) if a.id == action_id), -1)
        if idx < 0:
            raise TestDefinitionError(f"Action '{action_id}' not found in test '{defn.name}'.")
        target_idx = idx + delta
        if not (0 <= target_idx < len(actions)):
            return defn
        actions[idx], actions[target_idx] = actions[target_idx], actions[idx]
        reindexed = [a.model_copy(update={"order_index": i}) for i, a in enumerate(actions, 1)]
        updated = defn.model_copy(update={"actions": reindexed, "updated_at": datetime.now(UTC)})
        return self._definition_repo.update(updated)

    def delete_action(self, definition_id: str, action_id: str) -> TestDefinition:
        """Delete an action step and detach any assertions pointing to it."""
        defn = self._require_definition(definition_id)
        remaining = [a for a in defn.actions if a.id != action_id]
        if len(remaining) == len(defn.actions):
            raise TestDefinitionError(f"Action '{action_id}' not found in test '{defn.name}'.")
        reindexed = [a.model_copy(update={"order_index": i}) for i, a in enumerate(remaining, 1)]
        assertions = [
            a.model_copy(update={"after_action_id": None}) if a.after_action_id == action_id else a
            for a in defn.assertions
        ]
        updated = defn.model_copy(
            update={
                "actions": reindexed,
                "assertions": assertions,
                "updated_at": datetime.now(UTC),
            }
        )
        return self._definition_repo.update(updated)

    def add_assertion(self, definition_id: str, assertion: TestAssertion) -> TestDefinition:
        """Append a user-created assertion to a test definition."""
        defn = self._require_definition(definition_id)
        next_idx = len(defn.assertions) + 1
        new_item = assertion.model_copy(update={"order_index": next_idx})
        updated = defn.model_copy(
            update={
                "assertions": [*defn.assertions, new_item],
                "updated_at": datetime.now(UTC),
            }
        )
        return self._definition_repo.update(updated)

    def update_assertion(
        self,
        definition_id: str,
        assertion_id: str,
        *,
        description: str | None = None,
        assertion_type: AssertionType | None = None,
        expected: str | None = None,
        selector: SelectorCandidate | None = None,
        enabled: bool | None = None,
        needs_review: bool | None = None,
    ) -> TestDefinition:
        """Edit an existing assertion's properties or enable/disable it."""
        defn = self._require_definition(definition_id)
        new_assertions = []
        found = False
        for item in defn.assertions:
            if item.id == assertion_id:
                found = True
                patch: dict[str, object] = {}
                if description is not None and description.strip():
                    patch["description"] = description.strip()
                if assertion_type is not None:
                    patch["assertion_type"] = assertion_type
                if expected is not None:
                    patch["expected"] = expected
                if selector is not None:
                    patch["selector"] = selector
                if enabled is not None:
                    patch["enabled"] = enabled
                if needs_review is not None:
                    patch["needs_review"] = needs_review
                new_assertions.append(item.model_copy(update=patch))
            else:
                new_assertions.append(item)

        if not found:
            raise TestDefinitionError(
                f"Assertion '{assertion_id}' not found in test '{defn.name}'."
            )

        updated = defn.model_copy(
            update={"assertions": new_assertions, "updated_at": datetime.now(UTC)}
        )
        return self._definition_repo.update(updated)

    def delete_assertion(self, definition_id: str, assertion_id: str) -> TestDefinition:
        """Remove an assertion from a test definition."""
        defn = self._require_definition(definition_id)
        remaining = [a for a in defn.assertions if a.id != assertion_id]
        if len(remaining) == len(defn.assertions):
            raise TestDefinitionError(
                f"Assertion '{assertion_id}' not found in test '{defn.name}'."
            )
        reindexed = [a.model_copy(update={"order_index": i}) for i, a in enumerate(remaining, 1)]
        updated = defn.model_copy(update={"assertions": reindexed, "updated_at": datetime.now(UTC)})
        return self._definition_repo.update(updated)

    def delete_definition(self, definition_id: str) -> None:
        """Delete a test definition and re-index remaining ones in-place."""
        defn = self._require_definition(definition_id)
        self._definition_repo.delete(definition_id)
        remaining = self._definition_repo.list_by_session(defn.session_id)
        self._definition_repo.update_order_indices([d.id for d in remaining])

    def _require_definition(self, definition_id: str) -> TestDefinition:
        defn = self._definition_repo.get(definition_id)
        if defn is None:
            raise TestDefinitionError(f"Test definition '{definition_id}' not found.")
        return defn

    def _unique_function_name(
        self,
        session_id: str,
        name: str,
        *,
        exclude_id: str | None = None,
    ) -> str:
        existing = {
            d.function_name
            for d in self._definition_repo.list_by_session(session_id)
            if d.id != exclude_id
        }
        base = slugify_test_function_name(name)
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate
