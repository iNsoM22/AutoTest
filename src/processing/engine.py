"""Deterministic event processing engine: filtering, grouping, deduplication, and correlation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from src.processing.models import (
    CorrelatedOutcome,
    OutcomeType,
    ProcessedActionType,
    ProcessedEvent,
    ProcessingConfig,
    ProcessingResult,
)
from src.recording.models import EventType, RawEvent

_STATIC_RESOURCE_TYPES = frozenset({"image", "font", "stylesheet", "media", "manifest", "other"})
_INTERACTION_TYPES = frozenset(
    {
        EventType.CLICK,
        EventType.INPUT,
        EventType.CHANGE,
        EventType.SUBMIT,
        EventType.KEYDOWN,
        EventType.HOVER,
    }
)
_NAV_TRIGGER_ACTIONS = frozenset(
    {
        ProcessedActionType.CLICK,
        ProcessedActionType.SUBMIT,
        ProcessedActionType.PRESS_KEY,
    }
)
_KEEP_KEYS = frozenset({"Enter", "Escape"})


def _ms_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() * 1000.0


def _merge_ids(existing: list[str], extra: Sequence[str]) -> list[str]:
    seen = set(existing)
    merged = list(existing)
    for item in extra:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def _target_key(target: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(target.get("test_id", "")),
        str(target.get("id", "")),
        str(target.get("name", "")),
        str(target.get("css_path", "")),
        str(target.get("tag", "")),
    )


def _target_label(target: dict[str, Any]) -> str:
    for key in ("text", "aria_label", "placeholder", "test_id", "name", "id", "css_path", "tag"):
        val = target.get(key)
        if val:
            return str(val).strip()
    return "element"


def _short_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _summarize_action(
    action_type: ProcessedActionType,
    target: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    label = _target_label(target)
    if action_type == ProcessedActionType.NAVIGATE:
        return f"Navigate to {payload.get('url', '')}".strip()
    if action_type == ProcessedActionType.OPEN_TAB:
        return f"Open tab ({payload.get('url', 'about:blank')})"
    if action_type == ProcessedActionType.CLOSE_TAB:
        return f"Close tab ({payload.get('url', '')})"
    if action_type == ProcessedActionType.CLICK:
        return f"Click '{label}'"
    if action_type == ProcessedActionType.FILL:
        val = payload.get("value", "")
        return f"Fill '{label}' with '{val}'"
    if action_type == ProcessedActionType.SELECT:
        display = payload.get("selected_text") or payload.get("value", "")
        return f"Select '{display}' in '{label}'"
    if action_type == ProcessedActionType.CHECK:
        state = "Check" if payload.get("checked", True) else "Uncheck"
        return f"{state} '{label}'"
    if action_type == ProcessedActionType.SUBMIT:
        trigger = payload.get("trigger_label")
        if trigger:
            return f"Submit form via '{trigger}'"
        return f"Submit '{label}'"
    if action_type == ProcessedActionType.PRESS_KEY:
        key = payload.get("key", "")
        mods = [m for m in ("ctrl", "alt", "shift", "meta") if payload.get(m)]
        combo = "+".join([*mods, key]) if mods else str(key)
        return f"Press {combo} on '{label}'"
    if action_type == ProcessedActionType.HOVER:
        return f"Hover over '{label}'"
    if action_type == ProcessedActionType.SCROLL:
        return f"Scroll to ({payload.get('scroll_x', 0)}, {payload.get('scroll_y', 0)})"
    return f"{action_type.value} '{label}'"


class ProcessingEngine:
    """Converts immutable `RawEvent` streams into grouped, correlated `ProcessedEvent` timelines."""

    def __init__(self, config: ProcessingConfig | None = None) -> None:
        self._config = config or ProcessingConfig()

    def process(self, session_id: str, raw_events: Sequence[RawEvent]) -> ProcessingResult:
        """Run filtering, grouping, deduplication, and action-to-outcome correlation."""
        if not raw_events:
            return ProcessingResult(
                session_id=session_id,
                raw_event_count=0,
                processed_events=[],
                dropped_count=0,
                uncorrelated_outcomes=[],
            )

        ordered = sorted(raw_events, key=lambda e: (e.sequence_no, e.timestamp))
        notable_mutations_by_page = self._index_notable_mutations(ordered)

        kept_events: list[RawEvent] = []
        dropped_count = 0
        for event in ordered:
            if self._should_drop(event, notable_mutations_by_page):
                dropped_count += 1
            else:
                kept_events.append(event)

        actions, pending_outcomes = self._build_actions_and_outcomes(kept_events)
        uncorrelated = self._correlate_outcomes(actions, pending_outcomes)

        final_events: list[ProcessedEvent] = []
        for idx, action in enumerate(actions, start=1):
            summary = _summarize_action(action.action_type, action.target, action.payload)
            final_events.append(action.model_copy(update={"order_index": idx, "summary": summary}))

        return ProcessingResult(
            session_id=session_id,
            raw_event_count=len(raw_events),
            processed_events=final_events,
            dropped_count=dropped_count,
            uncorrelated_outcomes=uncorrelated,
        )

    def _index_notable_mutations(
        self, events: Sequence[RawEvent]
    ) -> dict[str | None, list[datetime]]:
        index: dict[str | None, list[datetime]] = {}
        for event in events:
            if event.event_type == EventType.DOM_MUTATION and event.payload.get("notable"):
                index.setdefault(event.page_id, []).append(event.timestamp)
        return index

    def _should_drop(
        self,
        event: RawEvent,
        notable_mutations_by_page: dict[str | None, list[datetime]],
    ) -> bool:
        etype = event.event_type
        payload = event.payload

        if (
            self._config.drop_untrusted
            and etype in _INTERACTION_TYPES
            and payload.get("is_trusted") is False
        ):
            return True

        if etype == EventType.SCROLL:
            return not self._config.keep_scrolls

        if etype == EventType.HOVER:
            if self._config.keep_hovers:
                return False
            # Keep hover only if followed closely by a notable DOM mutation on the same page
            timestamps = notable_mutations_by_page.get(event.page_id, [])
            for ts in timestamps:
                delta = _ms_between(event.timestamp, ts)
                if 0.0 <= delta <= self._config.hover_mutation_window_ms:
                    return False
            return True

        if etype == EventType.KEYDOWN:
            key = str(payload.get("key", ""))
            has_modifier = bool(payload.get("ctrl") or payload.get("alt") or payload.get("meta"))
            return key not in _KEEP_KEYS and not has_modifier

        if etype == EventType.DOM_MUTATION:
            notable = payload.get("notable")
            return not isinstance(notable, list) or len(notable) == 0

        if etype == EventType.CONSOLE:
            return str(payload.get("level", "")).lower() != "error"

        if etype == EventType.REQUEST:
            res_type = str(payload.get("resource_type", "")).lower()
            return bool(res_type in _STATIC_RESOURCE_TYPES or payload.get("is_navigation_request"))

        if etype == EventType.RESPONSE:
            res_type = str(payload.get("resource_type", "")).lower()
            status = int(payload.get("status", 200) or 200)
            if status >= 400:
                return False
            return res_type in _STATIC_RESOURCE_TYPES or res_type == "document"

        return False

    def _build_actions_and_outcomes(
        self,
        events: Sequence[RawEvent],
    ) -> tuple[list[ProcessedEvent], list[tuple[str | None, CorrelatedOutcome]]]:
        actions: list[ProcessedEvent] = []
        outcomes: list[tuple[str | None, CorrelatedOutcome]] = []
        requests_by_corr: dict[str, RawEvent] = {}

        for event in events:
            etype = event.event_type

            # Network events -> paired into CorrelatedOutcome
            if etype == EventType.REQUEST:
                if event.correlation_id:
                    requests_by_corr[event.correlation_id] = event
                continue

            if etype == EventType.RESPONSE:
                req = requests_by_corr.pop(event.correlation_id or "", None)
                raw_ids = [req.id, event.id] if req is not None else [event.id]
                method = str(event.payload.get("method", "GET"))
                status = int(event.payload.get("status", 0) or 0)
                url = str(event.payload.get("url", ""))
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.NETWORK,
                            summary=f"{method} {status} {_short_url(url)}".strip(),
                            details={
                                "method": method,
                                "url": url,
                                "status": status,
                                "ok": bool(event.payload.get("ok", 200 <= status < 400)),
                                "resource_type": event.payload.get("resource_type", ""),
                                "content_type": event.payload.get("content_type", ""),
                            },
                            raw_event_ids=raw_ids,
                            timestamp=event.timestamp,
                        ),
                    )
                )
                continue

            if etype == EventType.REQUEST_FAILED:
                req = requests_by_corr.pop(event.correlation_id or "", None)
                raw_ids = [req.id, event.id] if req is not None else [event.id]
                method = str(event.payload.get("method", "GET"))
                url = str(event.payload.get("url", ""))
                failure = str(event.payload.get("failure", "failed"))
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.NETWORK,
                            summary=f"{method} FAILED {_short_url(url)} ({failure})".strip(),
                            details={
                                "method": method,
                                "url": url,
                                "ok": False,
                                "failed": True,
                                "failure": failure,
                                "resource_type": event.payload.get("resource_type", ""),
                            },
                            raw_event_ids=raw_ids,
                            timestamp=event.timestamp,
                        ),
                    )
                )
                continue

            if etype == EventType.DOM_MUTATION:
                notable = event.payload.get("notable", [])
                texts = [str(n.get("text", "")).strip() for n in notable if n.get("text")]
                if not texts:
                    attr_notes = [
                        f"{n.get('id') or n.get('tag', 'element')} {n.get('attribute')} changed"
                        for n in notable
                        if isinstance(n, dict) and n.get("attribute")
                    ]
                    preview = "; ".join(attr_notes[:2]) if attr_notes else "DOM updated"
                else:
                    preview = "; ".join(texts[:2])
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.DOM_CHANGE,
                            summary=f"DOM: {preview}",
                            details={"notable": notable},
                            raw_event_ids=[event.id],
                            timestamp=event.timestamp,
                        ),
                    )
                )
                continue

            if etype == EventType.CONSOLE:
                text = str(event.payload.get("text", "")).strip()
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.CONSOLE_ERROR,
                            summary=f"Console error: {text[:120]}",
                            details=dict(event.payload),
                            raw_event_ids=[event.id],
                            timestamp=event.timestamp,
                        ),
                    )
                )
                continue

            if etype == EventType.PAGE_ERROR:
                msg = str(event.payload.get("message", "")).strip()
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.PAGE_ERROR,
                            summary=f"Page error: {msg[:120]}",
                            details=dict(event.payload),
                            raw_event_ids=[event.id],
                            timestamp=event.timestamp,
                        ),
                    )
                )
                continue

            # Browser lifecycle & user actions
            self._append_or_merge_action(actions, outcomes, event)

        return actions, outcomes

    def _append_or_merge_action(
        self,
        actions: list[ProcessedEvent],
        outcomes: list[tuple[str | None, CorrelatedOutcome]],
        event: RawEvent,
    ) -> None:
        etype = event.event_type
        last = actions[-1] if actions else None

        if etype == EventType.TAB_OPEN:
            tab_index = int(event.payload.get("tab_index", 0) or 0)
            url = str(event.payload.get("url", ""))
            action_type = (
                ProcessedActionType.NAVIGATE if tab_index == 0 else ProcessedActionType.OPEN_TAB
            )
            if action_type == ProcessedActionType.NAVIGATE and (not url or url == "about:blank"):
                # Wait for the real initial navigation rather than recording about:blank
                return
            actions.append(self._make_action(event, action_type))
            return

        if etype == EventType.TAB_CLOSE:
            actions.append(self._make_action(event, ProcessedActionType.CLOSE_TAB))
            return

        if etype == EventType.NAVIGATION:
            url = str(event.payload.get("url", ""))
            if not url or url == "about:blank":
                return
            # Deduplicate identical consecutive navigation on same page
            if (
                last is not None
                and last.page_id == event.page_id
                and last.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB)
                and last.payload.get("url") == url
            ):
                last.raw_event_ids = _merge_ids(last.raw_event_ids, [event.id])
                last.ended_at = max(last.ended_at, event.timestamp)
                if event.payload.get("title"):
                    last.payload["title"] = event.payload["title"]
                return

            # If navigation immediately follows a trigger action on the same page, treat as outcome
            if (
                last is not None
                and last.page_id == event.page_id
                and last.action_type in _NAV_TRIGGER_ACTIONS
                and 0.0
                <= _ms_between(last.ended_at, event.timestamp)
                <= self._config.correlation_window_ms
            ):
                outcomes.append(
                    (
                        event.page_id,
                        CorrelatedOutcome(
                            outcome_type=OutcomeType.NAVIGATION,
                            summary=f"Navigated to {_short_url(url)}",
                            details={
                                "url": url,
                                "title": event.payload.get("title", ""),
                            },
                            raw_event_ids=[event.id],
                            timestamp=event.timestamp,
                        ),
                    )
                )
                return

            actions.append(self._make_action(event, ProcessedActionType.NAVIGATE))
            return

        if etype == EventType.INPUT:
            if (
                last is not None
                and last.page_id == event.page_id
                and last.action_type == ProcessedActionType.FILL
                and _target_key(last.target) == _target_key(event.target)
                and _ms_between(last.ended_at, event.timestamp)
                <= self._config.input_merge_window_ms
            ):
                last.payload.update(event.payload)
                last.ended_at = max(last.ended_at, event.timestamp)
                last.raw_event_ids = _merge_ids(last.raw_event_ids, [event.id])
                return
            actions.append(self._make_action(event, ProcessedActionType.FILL))
            return

        if etype == EventType.CHANGE:
            tag = str(event.target.get("tag", "")).lower()
            input_type = str(event.target.get("input_type", "")).lower()

            if tag == "select":
                actions.append(self._make_action(event, ProcessedActionType.SELECT))
                return

            if input_type in ("checkbox", "radio"):
                # Collapse preceding click on the same checkbox/radio or its label
                if (
                    last is not None
                    and last.page_id == event.page_id
                    and last.action_type == ProcessedActionType.CLICK
                    and _ms_between(last.ended_at, event.timestamp) <= 300.0
                    and (
                        _target_key(last.target) == _target_key(event.target)
                        or str(last.target.get("tag", "")).lower() == "label"
                    )
                ):
                    last.action_type = ProcessedActionType.CHECK
                    last.target = dict(event.target)
                    last.payload = dict(event.payload)
                    last.ended_at = max(last.ended_at, event.timestamp)
                    last.raw_event_ids = _merge_ids(last.raw_event_ids, [event.id])
                    return
                actions.append(self._make_action(event, ProcessedActionType.CHECK))
                return

            # Text input change immediately after FILL on the same element -> merge
            if (
                last is not None
                and last.page_id == event.page_id
                and last.action_type == ProcessedActionType.FILL
                and _target_key(last.target) == _target_key(event.target)
            ):
                if "value" in event.payload:
                    last.payload["value"] = event.payload["value"]
                last.ended_at = max(last.ended_at, event.timestamp)
                last.raw_event_ids = _merge_ids(last.raw_event_ids, [event.id])
                return

            actions.append(self._make_action(event, ProcessedActionType.FILL))
            return

        if etype == EventType.SUBMIT:
            # Collapse preceding submit button click or Enter key press
            if (
                last is not None
                and last.page_id == event.page_id
                and last.action_type in (ProcessedActionType.CLICK, ProcessedActionType.PRESS_KEY)
                and _ms_between(last.ended_at, event.timestamp) <= 500.0
            ):
                merged_payload = dict(event.payload)
                if last.action_type == ProcessedActionType.CLICK:
                    merged_payload["trigger_label"] = _target_label(last.target)
                    merged_payload["trigger_target"] = dict(last.target)
                elif last.payload.get("key"):
                    merged_payload["trigger_key"] = last.payload["key"]
                last.action_type = ProcessedActionType.SUBMIT
                last.target = dict(event.target)
                last.payload = merged_payload
                last.ended_at = max(last.ended_at, event.timestamp)
                last.raw_event_ids = _merge_ids(last.raw_event_ids, [event.id])
                return
            actions.append(self._make_action(event, ProcessedActionType.SUBMIT))
            return

        if etype == EventType.CLICK:
            actions.append(self._make_action(event, ProcessedActionType.CLICK))
            return

        if etype == EventType.KEYDOWN:
            actions.append(self._make_action(event, ProcessedActionType.PRESS_KEY))
            return

        if etype == EventType.HOVER:
            actions.append(self._make_action(event, ProcessedActionType.HOVER))
            return

        if etype == EventType.SCROLL:
            actions.append(self._make_action(event, ProcessedActionType.SCROLL))

    @staticmethod
    def _make_action(event: RawEvent, action_type: ProcessedActionType) -> ProcessedEvent:
        return ProcessedEvent(
            project_id=event.project_id,
            session_id=event.session_id,
            page_id=event.page_id,
            order_index=1,
            action_type=action_type,
            summary="",
            target=dict(event.target),
            payload=dict(event.payload),
            outcomes=[],
            raw_event_ids=[event.id],
            started_at=event.timestamp,
            ended_at=event.timestamp,
        )

    def _correlate_outcomes(
        self,
        actions: list[ProcessedEvent],
        pending_outcomes: Sequence[tuple[str | None, CorrelatedOutcome]],
    ) -> list[CorrelatedOutcome]:
        uncorrelated: list[CorrelatedOutcome] = []
        window_ms = float(self._config.correlation_window_ms)
        action_end_times = {id(action): action.ended_at for action in actions}

        for page_id, outcome in pending_outcomes:
            candidate: ProcessedEvent | None = None
            for action in reversed(actions):
                if action.page_id != page_id:
                    continue
                if action.started_at > outcome.timestamp:
                    continue
                action_end = action_end_times[id(action)]
                delta = _ms_between(action_end, outcome.timestamp)
                if delta <= window_ms:
                    candidate = action
                break

            if candidate is not None:
                candidate.outcomes.append(outcome)
                candidate.ended_at = max(candidate.ended_at, outcome.timestamp)
                candidate.raw_event_ids = _merge_ids(candidate.raw_event_ids, outcome.raw_event_ids)
            else:
                uncorrelated.append(outcome)

        return uncorrelated
