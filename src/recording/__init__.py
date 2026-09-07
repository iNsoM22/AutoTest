"""Hybrid recording engine: unified events, redaction, collector, and observers."""

from src.recording.collector import EventCollector
from src.recording.engine import RecordingEngine
from src.recording.models import EventSource, EventType, RawEvent
from src.recording.page_observer import PageObserver
from src.recording.playwright_observer import PlaywrightObserver
from src.recording.redaction import (
    REDACTED_VALUE,
    is_sensitive_key,
    redact_string,
    redact_target_and_payload,
    redact_url,
)

__all__ = [
    "REDACTED_VALUE",
    "EventCollector",
    "EventSource",
    "EventType",
    "PageObserver",
    "PlaywrightObserver",
    "RawEvent",
    "RecordingEngine",
    "is_sensitive_key",
    "redact_string",
    "redact_target_and_payload",
    "redact_url",
]
