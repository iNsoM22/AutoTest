"""Thread-safe unified event collector with redaction and batched SQLite persistence."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.recording.models import EventSource, EventType, RawEvent
from src.recording.redaction import redact_target_and_payload

if TYPE_CHECKING:
    from src.storage.events import RawEventRepository

logger = logging.getLogger(__name__)


class EventCollector:
    """Normalizes, redacts, buffers, and batch-persists raw recording events."""

    def __init__(
        self,
        repository: RawEventRepository,
        *,
        project_id: str,
        session_id: str,
        batch_size: int = 50,
        on_event: Callable[[RawEvent], None] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1.")
        self._repository = repository
        self._project_id = project_id
        self._session_id = session_id
        self._batch_size = batch_size
        self._on_event = on_event

        self._lock = threading.Lock()
        self._sequence_no = 0
        self._total_recorded = 0
        self._buffer: list[RawEvent] = []

    @property
    def total_recorded(self) -> int:
        """Return total events accepted by this collector."""
        with self._lock:
            return self._total_recorded

    @property
    def buffered_count(self) -> int:
        """Return number of events currently waiting to be flushed."""
        with self._lock:
            return len(self._buffer)

    def record(
        self,
        *,
        event_type: EventType,
        source: EventSource,
        page_id: str | None = None,
        target: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> RawEvent:
        """Create a redacted `RawEvent`, buffer it, and flush when `batch_size` is reached."""
        clean_target, clean_payload = redact_target_and_payload(target or {}, payload or {})

        batch_to_flush: list[RawEvent] | None = None
        with self._lock:
            self._sequence_no += 1
            self._total_recorded += 1
            event = RawEvent(
                project_id=self._project_id,
                session_id=self._session_id,
                page_id=page_id,
                event_type=event_type,
                source=source,
                sequence_no=self._sequence_no,
                target=clean_target,
                payload=clean_payload,
                correlation_id=correlation_id,
            )
            self._buffer.append(event)
            if len(self._buffer) >= self._batch_size:
                batch_to_flush = self._buffer
                self._buffer = []

        if batch_to_flush is not None:
            self._persist_batch(batch_to_flush)

        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                logger.debug("Ignored error in event observer callback", exc_info=True)

        return event

    def flush(self) -> int:
        """Persist all buffered events immediately and return the count flushed."""
        with self._lock:
            if not self._buffer:
                return 0
            batch = self._buffer
            self._buffer = []
        return self._persist_batch(batch)

    def _persist_batch(self, batch: list[RawEvent]) -> int:
        try:
            return self._repository.insert_batch(batch)
        except Exception:
            with self._lock:
                self._buffer = batch + self._buffer
            raise
