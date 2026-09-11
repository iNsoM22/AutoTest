"""Versioned, idempotent SQLite schema migrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.storage.database import Database


@dataclass(frozen=True, slots=True)
class Migration:
    """A single forward-only SQLite schema migration."""

    version: int
    name: str
    sql: str


_MIGRATION_001_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recording_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recording_sessions_project_created
    ON recording_sessions (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recording_sessions_status
    ON recording_sessions (status);

CREATE TABLE IF NOT EXISTS browser_pages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tab_index INTEGER NOT NULL,
    initial_url TEXT NOT NULL DEFAULT '',
    last_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY (session_id) REFERENCES recording_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_browser_pages_session
    ON browser_pages (session_id, tab_index);

CREATE TABLE IF NOT EXISTS raw_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    page_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES recording_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES browser_pages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_events_session_seq
    ON raw_events (session_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_raw_events_session_ts
    ON raw_events (session_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_raw_events_session_type
    ON raw_events (session_id, event_type);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT,
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES recording_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_artifacts_project_session
    ON artifacts (project_id, session_id);
"""

_MIGRATION_002_SQL = """
CREATE TABLE IF NOT EXISTS processed_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    page_id TEXT,
    order_index INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    outcomes_json TEXT NOT NULL DEFAULT '[]',
    raw_event_ids_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES recording_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES browser_pages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_events_session_order
    ON processed_events (session_id, order_index);

CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL,
    included INTEGER NOT NULL DEFAULT 1,
    processed_event_ids_json TEXT NOT NULL DEFAULT '[]',
    raw_event_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES recording_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scenarios_session_order
    ON scenarios (session_id, order_index);

CREATE INDEX IF NOT EXISTS idx_scenarios_project
    ON scenarios (project_id);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="initial_schema",
        sql=_MIGRATION_001_SQL,
    ),
    Migration(
        version=2,
        name="processed_events_and_scenarios",
        sql=_MIGRATION_002_SQL,
    ),
)


def _ensure_migrations_table(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )


def get_schema_version(db: Database) -> int:
    """Return the highest applied migration version, or 0 if none applied."""
    _ensure_migrations_table(db)
    conn = db.connect()
    row = conn.execute("SELECT MAX(version) AS max_version FROM schema_migrations;").fetchone()
    if row is None or row["max_version"] is None:
        return 0
    return int(row["max_version"])


def run_migrations(db: Database) -> list[int]:
    """Apply all pending migrations in ascending version order.

    Returns the list of migration version numbers newly applied.
    """
    _ensure_migrations_table(db)
    conn = db.connect()
    rows = conn.execute("SELECT version FROM schema_migrations;").fetchall()
    applied_versions = {int(row["version"]) for row in rows}

    newly_applied: list[int] = []
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in applied_versions:
            continue

        now = datetime.now(UTC).isoformat()
        with db.transaction() as tx:
            for statement in _split_sql_statements(migration.sql):
                tx.execute(statement)
            tx.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?);
                """,
                (migration.version, migration.name, now),
            )
        newly_applied.append(migration.version)

    return newly_applied


def _split_sql_statements(sql: str) -> list[str]:
    """Split a migration script into non-empty SQL statements."""
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
