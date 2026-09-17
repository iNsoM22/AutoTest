"""Event filtering, grouping, deduplication, and action-to-outcome correlation."""

from src.processing.engine import ProcessingEngine
from src.processing.models import (
    CorrelatedOutcome,
    OutcomeType,
    ProcessedActionType,
    ProcessedEvent,
    ProcessingConfig,
    ProcessingResult,
)
from src.processing.repository import ProcessedEventRepository

__all__ = [
    "CorrelatedOutcome",
    "OutcomeType",
    "ProcessedActionType",
    "ProcessedEvent",
    "ProcessedEventRepository",
    "ProcessingConfig",
    "ProcessingEngine",
    "ProcessingResult",
]
