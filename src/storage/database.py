"""SQLite connection management with WAL mode, foreign keys, and transactions."""

from __future__ import annotations

import logging
import sqlite3
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

logger = logging.getLogger(__name__)


class Database:
    """Thread-safe SQLite database manager for AutoTest.

    Maintains one SQLite connection per thread with consistent pragmas
    (`foreign_keys = ON`, optional `journal_mode = WAL`, and `busy_timeout`).
    """

    _instances_lock = threading.Lock()
    _instances: weakref.WeakSet[Database] = weakref.WeakSet()

    def __init__(
        self,
        db_path: Path,
        *,
        wal_mode: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._db_path = db_path
        self._wal_mode = wal_mode
        self._timeout_seconds = timeout_seconds
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}
        with self._instances_lock:
            self._instances.add(self)

    @property
    def db_path(self) -> Path:
        """Return the path to the SQLite database file."""
        return self._db_path

    @property
    def wal_mode(self) -> bool:
        """Return whether WAL journal mode is enabled."""
        return self._wal_mode

    def connect(self) -> sqlite3.Connection:
        """Return the current thread's SQLite connection, creating it if needed."""
        conn: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if conn is not None:
            return conn

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=self._timeout_seconds,
            isolation_level=None,  # Explicit transaction control via `transaction()`
        )
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)

        self._local.connection = conn
        tid = threading.get_ident()
        with self._lock:
            self._connections[tid] = conn
        return conn

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """Apply required SQLite pragmas to a newly opened connection."""
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)};")
        if self._wal_mode:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        else:
            conn.execute("PRAGMA journal_mode = DELETE;")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Execute a block inside an explicit IMMEDIATE SQLite transaction."""
        conn = self.connect()
        if conn.in_transaction:
            yield conn
            return

        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise

    def initialize(self) -> list[int]:
        """Ensure the database file exists and apply all pending schema migrations."""
        from src.storage.migrations import run_migrations

        self.connect()
        applied = run_migrations(self)
        if applied:
            logger.info("Applied database migrations: %s", applied)
        return applied

    def close(self) -> None:
        """Close the SQLite connection owned by the current thread, if any."""
        conn: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if conn is None:
            return
        self._local.connection = None
        tid = threading.get_ident()
        with self._lock:
            self._connections.pop(tid, None)
        with suppress(sqlite3.Error):
            conn.close()

    @classmethod
    def close_current_thread_connections(cls) -> None:
        """Close SQLite connections owned by the current thread across all Database instances."""
        with cls._instances_lock:
            instances = list(cls._instances)
        for instance in instances:
            instance.close()

    def close_all(self) -> None:
        """Close the current thread's connection and attempt cleanup of any remaining ones.

        Note: SQLite connections are thread-affine (`check_same_thread=True`), so connections
        opened on other threads cannot be closed from here and must be closed on their owning
        thread via `close_current_thread_connections()`.
        """
        self.close()
        with self._lock:
            remaining = list(self._connections.values())
            self._connections.clear()
        for conn in remaining:
            try:
                conn.close()
            except sqlite3.Error:
                logger.debug("Ignored error closing SQLite connection", exc_info=True)
