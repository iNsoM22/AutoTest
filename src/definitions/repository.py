"""SQLite repository for persisted editable test definitions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from src.definitions.models import (
    TestAction,
    TestAssertion,
    TestDefinition,
    TestExecutionSettings,
    TestMode,
    TestPrerequisite,
)
from src.storage.database import Database


class TestDefinitionRepository:
    """Persistence adapter for the `test_definitions` table."""

    __test__ = False

    def __init__(self, db: Database) -> None:
        self._db = db

    def replace_for_session(
        self,
        session_id: str,
        definitions: Sequence[TestDefinition],
    ) -> list[TestDefinition]:
        """Atomically replace all test definitions for a recording session."""
        rows = [self._to_row(defn) for defn in definitions]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM test_definitions WHERE session_id = ?;", (session_id,))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO test_definitions (
                        id, project_id, session_id, scenario_id, name, function_name,
                        description, mode, order_index, included, prerequisites_json,
                        actions_json, assertions_json, settings_json, raw_event_ids_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
        return list(definitions)

    def get(self, definition_id: str) -> TestDefinition | None:
        """Fetch a single test definition by ID."""
        row = (
            self._db.connect()
            .execute("SELECT * FROM test_definitions WHERE id = ?;", (definition_id,))
            .fetchone()
        )
        return self._row_to_model(row) if row is not None else None

    def list_by_session(self, session_id: str) -> list[TestDefinition]:
        """List all test definitions for a recording session in display order."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM test_definitions
                WHERE session_id = ?
                ORDER BY order_index ASC, created_at ASC;
                """,
                (session_id,),
            )
            .fetchall()
        )
        return [self._row_to_model(row) for row in rows]

    def list_by_project(self, project_id: str) -> list[TestDefinition]:
        """List all test definitions across all sessions of a project."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM test_definitions
                WHERE project_id = ?
                ORDER BY created_at DESC, order_index ASC;
                """,
                (project_id,),
            )
            .fetchall()
        )
        return [self._row_to_model(row) for row in rows]

    def update(self, definition: TestDefinition) -> TestDefinition:
        """Persist updates to an existing test definition."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE test_definitions
                SET name = ?,
                    function_name = ?,
                    description = ?,
                    mode = ?,
                    order_index = ?,
                    included = ?,
                    prerequisites_json = ?,
                    actions_json = ?,
                    assertions_json = ?,
                    settings_json = ?,
                    raw_event_ids_json = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    definition.name,
                    definition.function_name,
                    definition.description,
                    definition.mode.value,
                    definition.order_index,
                    1 if definition.included else 0,
                    json.dumps([p.model_dump(mode="json") for p in definition.prerequisites]),
                    json.dumps([a.model_dump(mode="json") for a in definition.actions]),
                    json.dumps([a.model_dump(mode="json") for a in definition.assertions]),
                    json.dumps(definition.settings.model_dump(mode="json")),
                    json.dumps(definition.raw_event_ids),
                    definition.updated_at.isoformat(),
                    definition.id,
                ),
            )
        return definition

    def delete(self, definition_id: str) -> bool:
        """Delete a single test definition by ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM test_definitions WHERE id = ?;", (definition_id,))
        return cursor.rowcount > 0

    def delete_by_session(self, session_id: str) -> int:
        """Delete all test definitions for a session."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM test_definitions WHERE session_id = ?;", (session_id,)
            )
        return int(cursor.rowcount)

    @staticmethod
    def _to_row(defn: TestDefinition) -> tuple[object, ...]:
        return (
            defn.id,
            defn.project_id,
            defn.session_id,
            defn.scenario_id,
            defn.name,
            defn.function_name,
            defn.description,
            defn.mode.value,
            defn.order_index,
            1 if defn.included else 0,
            json.dumps([p.model_dump(mode="json") for p in defn.prerequisites]),
            json.dumps([a.model_dump(mode="json") for a in defn.actions]),
            json.dumps([a.model_dump(mode="json") for a in defn.assertions]),
            json.dumps(defn.settings.model_dump(mode="json")),
            json.dumps(defn.raw_event_ids),
            defn.created_at.isoformat(),
            defn.updated_at.isoformat(),
        )

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> TestDefinition:
        return TestDefinition(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            scenario_id=row["scenario_id"],
            name=row["name"],
            function_name=row["function_name"],
            description=row["description"],
            mode=TestMode(row["mode"]),
            order_index=int(row["order_index"]),
            included=bool(row["included"]),
            prerequisites=[
                TestPrerequisite.model_validate(item)
                for item in json.loads(row["prerequisites_json"])
            ],
            actions=[TestAction.model_validate(item) for item in json.loads(row["actions_json"])],
            assertions=[
                TestAssertion.model_validate(item) for item in json.loads(row["assertions_json"])
            ],
            settings=TestExecutionSettings.model_validate(json.loads(row["settings_json"])),
            raw_event_ids=json.loads(row["raw_event_ids_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
