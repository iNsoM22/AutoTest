"""Tests for SQLite database initialization, migrations, and artifact storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config import AppSettings
from src.storage import ArtifactManager, Database, get_schema_version, run_migrations


def test_database_initializes_with_wal_and_foreign_keys(tmp_settings: AppSettings) -> None:
    db = Database(tmp_settings.db_path, wal_mode=True)
    try:
        applied = db.initialize()
        assert applied == [1, 2]
        assert get_schema_version(db) == 2

        conn = db.connect()
        fk_enabled = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert fk_enabled == 1
        assert str(journal_mode).lower() == "wal"

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        }
        assert {
            "schema_migrations",
            "projects",
            "recording_sessions",
            "browser_pages",
            "raw_events",
            "artifacts",
            "processed_events",
            "scenarios",
        }.issubset(tables)
    finally:
        db.close_all()


def test_migrations_are_idempotent(tmp_settings: AppSettings) -> None:
    db = Database(tmp_settings.db_path)
    try:
        assert run_migrations(db) == [1, 2]
        assert run_migrations(db) == []
        assert get_schema_version(db) == 2
    finally:
        db.close_all()


def test_foreign_key_cascade_deletes_sessions(tmp_settings: AppSettings) -> None:
    db = Database(tmp_settings.db_path)
    try:
        db.initialize()
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, description, base_url, created_at, updated_at)
                VALUES ('p1', 'Demo', '', 'https://example.com', '2026-01-01T00:00:00Z',
                        '2026-01-01T00:00:00Z');
                """
            )
            conn.execute(
                """
                INSERT INTO recording_sessions
                    (id, project_id, name, start_url, status, created_at, updated_at)
                VALUES ('s1', 'p1', 'Session 1', 'https://example.com', 'stopped',
                        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
                """
            )

        with db.transaction() as conn:
            conn.execute("DELETE FROM projects WHERE id = 'p1';")

        remaining = db.connect().execute("SELECT COUNT(*) FROM recording_sessions;").fetchone()[0]
        assert remaining == 0
    finally:
        db.close_all()


def test_transaction_rolls_back_on_error(tmp_settings: AppSettings) -> None:
    db = Database(tmp_settings.db_path)
    try:
        db.initialize()
        with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, description, base_url, created_at, updated_at)
                VALUES ('p1', 'UniqueName', '', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
                """
            )
            # Duplicate name triggers UNIQUE constraint error
            conn.execute(
                """
                INSERT INTO projects (id, name, description, base_url, created_at, updated_at)
                VALUES ('p2', 'UniqueName', '', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
                """
            )

        count = db.connect().execute("SELECT COUNT(*) FROM projects;").fetchone()[0]
        assert count == 0
    finally:
        db.close_all()


def test_artifact_manager_paths_and_traversal_protection(tmp_path: Path) -> None:
    manager = ArtifactManager(tmp_path / "artifacts")
    session_dir = manager.ensure_session_dir("proj-1", "sess-1")
    assert session_dir.is_dir()

    rel = manager.to_relative_path(session_dir / "trace.zip")
    assert rel == "projects/proj-1/sessions/sess-1/trace.zip"
    assert manager.resolve_path(rel) == (session_dir / "trace.zip").resolve()

    with pytest.raises(ValueError, match="escapes base directory"):
        manager.resolve_path("../../outside.txt")

    with pytest.raises(ValueError, match="Invalid artifact path segment"):
        manager.ensure_project_dir("../bad")
