"""SQLite repository for detected and user-edited scenarios."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from src.scenarios.models import Scenario
from src.storage.database import Database


class ScenarioRepository:
    """Persistence layer for `scenarios`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def replace_for_session(
        self,
        session_id: str,
        scenarios: Sequence[Scenario],
    ) -> list[Scenario]:
        """Atomically replace all scenarios for a recording session."""
        rows = [self._to_row(s) for s in scenarios]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM scenarios WHERE session_id = ?;", (session_id,))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO scenarios (
                        id, project_id, session_id, name, description, order_index,
                        included, processed_event_ids_json, raw_event_ids_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
        return list(scenarios)

    def create(self, scenario: Scenario) -> Scenario:
        """Insert a single scenario."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scenarios (
                    id, project_id, session_id, name, description, order_index,
                    included, processed_event_ids_json, raw_event_ids_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                self._to_row(scenario),
            )
        return scenario

    def get(self, scenario_id: str) -> Scenario | None:
        """Fetch a scenario by ID, or None if not found."""
        row = (
            self._db.connect()
            .execute("SELECT * FROM scenarios WHERE id = ?;", (scenario_id,))
            .fetchone()
        )
        return self._row_to_scenario(row) if row is not None else None

    def list_by_session(self, session_id: str) -> list[Scenario]:
        """List all scenarios for a session ordered by `order_index`."""
        rows = (
            self._db.connect()
            .execute(
                "SELECT * FROM scenarios WHERE session_id = ? ORDER BY order_index ASC;",
                (session_id,),
            )
            .fetchall()
        )
        return [self._row_to_scenario(row) for row in rows]

    def list_by_project(self, project_id: str) -> list[Scenario]:
        """List all scenarios for a project ordered by creation and `order_index`."""
        rows = (
            self._db.connect()
            .execute(
                """
                SELECT * FROM scenarios
                WHERE project_id = ?
                ORDER BY created_at DESC, order_index ASC;
                """,
                (project_id,),
            )
            .fetchall()
        )
        return [self._row_to_scenario(row) for row in rows]

    def update(self, scenario: Scenario) -> Scenario:
        """Update an existing scenario row."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE scenarios
                SET name = ?,
                    description = ?,
                    order_index = ?,
                    included = ?,
                    processed_event_ids_json = ?,
                    raw_event_ids_json = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    scenario.name,
                    scenario.description,
                    scenario.order_index,
                    1 if scenario.included else 0,
                    json.dumps(scenario.processed_event_ids, ensure_ascii=False),
                    json.dumps(scenario.raw_event_ids, ensure_ascii=False),
                    scenario.updated_at.isoformat(),
                    scenario.id,
                ),
            )
        return scenario

    def save_batch(
        self,
        *,
        to_update: Sequence[Scenario] = (),
        to_insert: Sequence[Scenario] = (),
        to_delete_ids: Sequence[str] = (),
    ) -> None:
        """Atomically apply updates, inserts, and deletes without severing surviving FKs."""
        with self._db.transaction() as conn:
            for scenario_id in to_delete_ids:
                conn.execute("DELETE FROM scenarios WHERE id = ?;", (scenario_id,))
            if to_update:
                conn.executemany(
                    """
                    UPDATE scenarios
                    SET name = ?,
                        description = ?,
                        order_index = ?,
                        included = ?,
                        processed_event_ids_json = ?,
                        raw_event_ids_json = ?,
                        updated_at = ?
                    WHERE id = ?;
                    """,
                    [
                        (
                            s.name,
                            s.description,
                            s.order_index,
                            1 if s.included else 0,
                            json.dumps(s.processed_event_ids, ensure_ascii=False),
                            json.dumps(s.raw_event_ids, ensure_ascii=False),
                            s.updated_at.isoformat(),
                            s.id,
                        )
                        for s in to_update
                    ],
                )
            if to_insert:
                conn.executemany(
                    """
                    INSERT INTO scenarios (
                        id, project_id, session_id, name, description, order_index,
                        included, processed_event_ids_json, raw_event_ids_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [self._to_row(s) for s in to_insert],
                )

    def delete(self, scenario_id: str) -> bool:
        """Delete a scenario by ID."""
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM scenarios WHERE id = ?;", (scenario_id,))
            return cur.rowcount > 0

    def delete_by_session(self, session_id: str) -> int:
        """Delete all scenarios belonging to a session."""
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM scenarios WHERE session_id = ?;", (session_id,))
            return cur.rowcount

    @staticmethod
    def _to_row(s: Scenario) -> tuple[object, ...]:
        return (
            s.id,
            s.project_id,
            s.session_id,
            s.name,
            s.description,
            s.order_index,
            1 if s.included else 0,
            json.dumps(s.processed_event_ids, ensure_ascii=False),
            json.dumps(s.raw_event_ids, ensure_ascii=False),
            s.created_at.isoformat(),
            s.updated_at.isoformat(),
        )

    @staticmethod
    def _row_to_scenario(row: sqlite3.Row) -> Scenario:
        return Scenario(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            name=row["name"],
            description=row["description"],
            order_index=int(row["order_index"]),
            included=bool(row["included"]),
            processed_event_ids=json.loads(row["processed_event_ids_json"] or "[]"),
            raw_event_ids=json.loads(row["raw_event_ids_json"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
