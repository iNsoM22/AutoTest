"""SQLite repository for immutable raw recording events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from src.recording.models import EventSource, EventType, RawEvent
from src.storage.database import Database


class RawEventRepository:
    """Batched SQLite persistence and query layer for `raw_events`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_batch(self, events: Sequence[RawEvent]) -> int:
        """Persist a batch of raw events in a single SQLite transaction."""
        if not events:
            return 0

        rows = [
            (
                event.id,
                event.project_id,
                event.session_id,
                event.page_id,
                event.event_type.value,
                event.source.value,
                event.timestamp.isoformat(),
                event.sequence_no,
                json.dumps(event.target, ensure_ascii=False),
                json.dumps(event.payload, ensure_ascii=False),
                event.correlation_id,
            )
            for event in events
        ]
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO raw_events (
                    id, project_id, session_id, page_id, event_type, source,
                    timestamp, sequence_no, target_json, payload_json, correlation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
        return len(rows)

    def list_by_session(
        self,
        session_id: str,
        *,
        event_type: EventType | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawEvent]:
        """Return raw events for a session ordered by `sequence_no`."""
        query = "SELECT * FROM raw_events WHERE session_id = ?"
        params: list[object] = [session_id]

        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type.value)

        query += " ORDER BY sequence_no ASC"

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        rows = self._db.connect().execute(query + ";", params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def count_by_session(self, session_id: str) -> int:
        """Return the total number of raw events recorded for a session."""
        row = (
            self._db.connect()
            .execute(
                "SELECT COUNT(*) AS cnt FROM raw_events WHERE session_id = ?;",
                (session_id,),
            )
            .fetchone()
        )
        return int(row["cnt"]) if row is not None else 0

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RawEvent:
        return RawEvent(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            page_id=row["page_id"],
            event_type=EventType(row["event_type"]),
            source=EventSource(row["source"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            sequence_no=int(row["sequence_no"]),
            target=json.loads(row["target_json"] or "{}"),
            payload=json.loads(row["payload_json"] or "{}"),
            correlation_id=row["correlation_id"],
        )
