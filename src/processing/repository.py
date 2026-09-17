"""SQLite repository for processed timeline events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from src.processing.models import CorrelatedOutcome, ProcessedActionType, ProcessedEvent
from src.storage.database import Database


class ProcessedEventRepository:
    """Persistence layer for `processed_events` (derived from immutable `raw_events`)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def replace_for_session(
        self,
        session_id: str,
        events: Sequence[ProcessedEvent],
    ) -> list[ProcessedEvent]:
        """Atomically replace all processed events for a session."""
        rows = [
            (
                event.id,
                event.project_id,
                event.session_id,
                event.page_id,
                event.order_index,
                event.action_type.value,
                event.summary,
                json.dumps(event.target, ensure_ascii=False),
                json.dumps(event.payload, ensure_ascii=False),
                json.dumps(
                    [o.model_dump(mode="json") for o in event.outcomes],
                    ensure_ascii=False,
                ),
                json.dumps(event.raw_event_ids, ensure_ascii=False),
                event.started_at.isoformat(),
                event.ended_at.isoformat(),
            )
            for event in events
        ]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM processed_events WHERE session_id = ?;", (session_id,))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO processed_events (
                        id, project_id, session_id, page_id, order_index, action_type,
                        summary, target_json, payload_json, outcomes_json,
                        raw_event_ids_json, started_at, ended_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
        return list(events)

    def list_by_session(self, session_id: str) -> list[ProcessedEvent]:
        """Return processed events for a session ordered by `order_index`."""
        rows = (
            self._db.connect()
            .execute(
                "SELECT * FROM processed_events WHERE session_id = ? ORDER BY order_index ASC;",
                (session_id,),
            )
            .fetchall()
        )
        return [self._row_to_event(row) for row in rows]

    def count_by_session(self, session_id: str) -> int:
        """Return the number of processed events stored for a session."""
        row = (
            self._db.connect()
            .execute(
                "SELECT COUNT(*) AS cnt FROM processed_events WHERE session_id = ?;",
                (session_id,),
            )
            .fetchone()
        )
        return int(row["cnt"]) if row is not None else 0

    def delete_by_session(self, session_id: str) -> int:
        """Delete all processed events for a session."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM processed_events WHERE session_id = ?;",
                (session_id,),
            )
            return cur.rowcount

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ProcessedEvent:
        raw_outcomes = json.loads(row["outcomes_json"] or "[]")
        outcomes = [CorrelatedOutcome.model_validate(item) for item in raw_outcomes]
        return ProcessedEvent(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            page_id=row["page_id"],
            order_index=int(row["order_index"]),
            action_type=ProcessedActionType(row["action_type"]),
            summary=row["summary"],
            target=json.loads(row["target_json"] or "{}"),
            payload=json.loads(row["payload_json"] or "{}"),
            outcomes=outcomes,
            raw_event_ids=json.loads(row["raw_event_ids_json"] or "[]"),
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
        )
