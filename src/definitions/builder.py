"""Deterministic rule-based builder converting reviewed scenarios into editable test definitions."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from src.definitions.models import (
    AssertionConfidence,
    AssertionType,
    SelectorCandidate,
    SelectorStrategy,
    TestAction,
    TestAssertion,
    TestDefinition,
    TestExecutionSettings,
    TestMode,
    TestPrerequisite,
    slugify_test_function_name,
)
from src.definitions.selectors import build_selector_candidates
from src.processing.models import (
    CorrelatedOutcome,
    OutcomeType,
    ProcessedActionType,
    ProcessedEvent,
)
from src.scenarios.models import Scenario
from src.utils import dedupe_preserve

_AUTH_KEYWORDS = ("login", "log in", "sign in", "signin", "authenticate", "auth")
_AUTH_FIELD_KEYWORDS = ("password", "passwd", "pwd", "passcode", "login", "signin", "auth")
_REDACTED_MARKER = "[REDACTED]"
_MODIFIER_KEY_MAP = {
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
}


def _url_path_fragment(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        query_upper = parsed.query.upper()
        if _REDACTED_MARKER in parsed.query or "%5BREDACTED%5D" in query_upper:
            return path
        return f"{path}?{parsed.query}"
    return path


def _is_auth_redacted_event(event: ProcessedEvent) -> bool:
    if not (event.payload.get("redacted") or event.payload.get("value") == _REDACTED_MARKER):
        return False
    target = event.target
    if str(target.get("input_type", "")).lower() == "password":
        return True
    autocomplete = str(target.get("autocomplete", "")).lower()
    if "password" in autocomplete:
        return True
    if "cc-" in autocomplete:
        return False
    field_text = " ".join(
        str(target.get(k, "")).lower() for k in ("name", "id", "aria_label", "label", "placeholder")
    )
    return any(kw in field_text for kw in _AUTH_FIELD_KEYWORDS)


def _looks_like_auth_scenario(scenario: Scenario, events: Sequence[ProcessedEvent]) -> bool:
    name_lower = scenario.name.lower()
    if any(kw in name_lower for kw in _AUTH_KEYWORDS):
        return True
    for event in events:
        if _is_auth_redacted_event(event):
            return True
        url = str(event.payload.get("url", "")).lower()
        if any(kw in url for kw in ("/login", "/signin", "/auth")):
            return True
    return False


class TestDefinitionBuilder:
    """Builds independent and complete-journey `TestDefinition`s purely from recorded evidence.

    Core rules:
    - Never invent unperformed scenarios or negative/boundary test cases.
    - Never treat HTTP 200 alone as proof of workflow success; require visible UI/DOM or
      navigation confirmation, and flag network-only outcomes as `inferred` + `needs_review`.
    - Classify every assertion as `directly_observed`, `correlated`, or `inferred`.
    - Declare explicit prerequisites for independent tests that rely on prior URL or auth state.
    """

    __test__ = False

    def build(
        self,
        *,
        project_id: str,
        session_id: str,
        scenarios: Sequence[Scenario],
        timeline: Sequence[ProcessedEvent],
        base_url: str = "",
        session_start_url: str = "",
        include_journey: bool = True,
    ) -> list[TestDefinition]:
        """Generate editable test definitions for all included scenarios in a session."""
        included_scenarios = [
            s for s in sorted(scenarios, key=lambda s: s.order_index) if s.included
        ]
        if not included_scenarios:
            return []

        timeline_by_id = {e.id: e for e in timeline}
        definitions: list[TestDefinition] = []
        used_func_names: set[str] = set()
        now = datetime.now(UTC)

        running_url = session_start_url or base_url
        prior_auth_seen = False

        for sc in included_scenarios:
            events = [
                timeline_by_id[eid] for eid in sc.processed_event_ids if eid in timeline_by_id
            ]
            if not events:
                continue

            prerequisites: list[TestPrerequisite] = []
            first_action = events[0]
            if first_action.action_type != ProcessedActionType.NAVIGATE and running_url:
                prerequisites.append(
                    TestPrerequisite(
                        name="entry_url",
                        description=f"Open {running_url} before running this independent test.",
                        fixture_name="page",
                        setup_url=running_url,
                    )
                )
            if prior_auth_seen:
                prerequisites.append(
                    TestPrerequisite(
                        name="authenticated_session",
                        description=(
                            "Requires authenticated browser state established in an earlier "
                            "recorded scenario. Configure the authenticated_page fixture."
                        ),
                        fixture_name="authenticated_page",
                    )
                )

            actions, assertions, raw_ids = self._convert_events(events)
            func_name = self._unique_func_name(sc.name, used_func_names)
            definitions.append(
                TestDefinition(
                    project_id=project_id,
                    session_id=session_id,
                    scenario_id=sc.id,
                    name=sc.name,
                    function_name=func_name,
                    description=sc.description or f"Independent test for scenario '{sc.name}'.",
                    mode=TestMode.INDEPENDENT,
                    order_index=len(definitions) + 1,
                    included=True,
                    prerequisites=prerequisites,
                    actions=actions,
                    assertions=assertions,
                    settings=TestExecutionSettings(base_url=base_url or session_start_url),
                    raw_event_ids=raw_ids,
                    created_at=now,
                    updated_at=now,
                )
            )

            # Advance tracked state for subsequent independent scenarios
            running_url = self._last_observed_url(events, fallback=running_url)
            if _looks_like_auth_scenario(sc, events):
                prior_auth_seen = True

        if include_journey and definitions:
            journey_events = [
                timeline_by_id[eid]
                for sc in included_scenarios
                for eid in sc.processed_event_ids
                if eid in timeline_by_id
            ]
            if journey_events:
                journey_prereqs: list[TestPrerequisite] = []
                if journey_events[0].action_type != ProcessedActionType.NAVIGATE and (
                    session_start_url or base_url
                ):
                    start = session_start_url or base_url
                    journey_prereqs.append(
                        TestPrerequisite(
                            name="entry_url",
                            description=f"Open {start} at the start of the journey.",
                            fixture_name="page",
                            setup_url=start,
                        )
                    )
                j_actions, j_assertions, j_raw_ids = self._convert_events(journey_events)
                journey_name = "Complete Recorded Journey"
                journey_func = self._unique_func_name(journey_name, used_func_names)
                scenario_names = ", ".join(sc.name for sc in included_scenarios)
                definitions.append(
                    TestDefinition(
                        project_id=project_id,
                        session_id=session_id,
                        scenario_id=None,
                        name=journey_name,
                        function_name=journey_func,
                        description=f"Sequential end-to-end journey covering: {scenario_names}.",
                        mode=TestMode.JOURNEY,
                        order_index=len(definitions) + 1,
                        included=True,
                        prerequisites=journey_prereqs,
                        actions=j_actions,
                        assertions=j_assertions,
                        settings=TestExecutionSettings(base_url=base_url or session_start_url),
                        raw_event_ids=j_raw_ids,
                        created_at=now,
                        updated_at=now,
                    )
                )

        return definitions

    def _convert_events(
        self,
        events: Sequence[ProcessedEvent],
    ) -> tuple[list[TestAction], list[TestAssertion], list[str]]:
        actions: list[TestAction] = []
        assertions: list[TestAssertion] = []
        all_raw_ids: list[str] = []

        for event in events:
            action = self._build_action(event, order_index=len(actions) + 1)
            actions.append(action)
            all_raw_ids.extend(event.raw_event_ids)

            step_assertions = self._build_assertions_for_event(
                event,
                action,
                start_order=len(assertions) + 1,
            )
            assertions.extend(step_assertions)
            for item in step_assertions:
                all_raw_ids.extend(item.raw_event_ids)

        return actions, assertions, dedupe_preserve(all_raw_ids)

    def _build_action(self, event: ProcessedEvent, *, order_index: int) -> TestAction:
        effective_target = event.target
        if event.action_type == ProcessedActionType.SUBMIT and isinstance(
            event.payload.get("trigger_target"), dict
        ):
            effective_target = event.payload["trigger_target"]

        primary, fallbacks = build_selector_candidates(effective_target)

        value = ""
        url = ""
        key = ""
        checked: bool | None = None
        is_redacted = False
        via_form_submit = False

        if event.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB):
            url = str(event.payload.get("url", ""))
        elif event.action_type == ProcessedActionType.FILL:
            raw_val = str(event.payload.get("value", ""))
            is_redacted = bool(event.payload.get("redacted")) or raw_val == _REDACTED_MARKER
            value = "" if is_redacted else raw_val
        elif event.action_type == ProcessedActionType.SELECT:
            value = str(event.payload.get("value") or event.payload.get("selected_text") or "")
        elif event.action_type == ProcessedActionType.CHECK:
            checked = bool(event.payload.get("checked", True))
        elif event.action_type == ProcessedActionType.PRESS_KEY:
            raw_key = str(event.payload.get("key", ""))
            mods = [
                _MODIFIER_KEY_MAP[m]
                for m in ("ctrl", "alt", "shift", "meta")
                if event.payload.get(m) and _MODIFIER_KEY_MAP[m].lower() != raw_key.lower()
            ]
            key = "+".join([*mods, raw_key]) if mods and raw_key else raw_key
        elif event.action_type == ProcessedActionType.SCROLL:
            sx = int(event.payload.get("scroll_x", 0) or 0)
            sy = int(event.payload.get("scroll_y", 0) or 0)
            value = f"{sx},{sy}"
        elif event.action_type == ProcessedActionType.SUBMIT:
            key = str(event.payload.get("trigger_key", ""))
            has_trigger_target = isinstance(event.payload.get("trigger_target"), dict)
            if (
                not key
                and not has_trigger_target
                and str(effective_target.get("tag", "")).strip().lower() == "form"
            ):
                via_form_submit = True

        return TestAction(
            order_index=order_index,
            action_type=event.action_type,
            summary=event.summary,
            selector=primary,
            fallback_selectors=fallbacks,
            value=value,
            url=url,
            key=key,
            checked=checked,
            is_redacted=is_redacted,
            via_form_submit=via_form_submit,
            source_processed_event_id=event.id,
            raw_event_ids=list(event.raw_event_ids),
        )

    def _build_assertions_for_event(
        self,
        event: ProcessedEvent,
        action: TestAction,
        *,
        start_order: int,
    ) -> list[TestAssertion]:
        assertions: list[TestAssertion] = []
        next_order = start_order

        nav_outcomes = [o for o in event.outcomes if o.outcome_type == OutcomeType.NAVIGATION]
        dom_outcomes = [o for o in event.outcomes if o.outcome_type == OutcomeType.DOM_CHANGE]
        net_outcomes = [o for o in event.outcomes if o.outcome_type == OutcomeType.NETWORK]
        err_outcomes = [
            o
            for o in event.outcomes
            if o.outcome_type in (OutcomeType.CONSOLE_ERROR, OutcomeType.PAGE_ERROR)
        ]

        has_network_ok = any(bool(o.details.get("ok")) for o in net_outcomes)
        has_error = bool(err_outcomes) or any(
            o.details.get("failed") or int(o.details.get("status", 200) or 200) >= 400
            for o in net_outcomes
        )

        # 1. Direct state assertion for checkbox/radio toggles
        if event.action_type == ProcessedActionType.CHECK and action.selector is not None:
            expected_state = "true" if action.checked else "false"
            assertions.append(
                TestAssertion(
                    order_index=next_order,
                    assertion_type=AssertionType.ELEMENT_CHECKED,
                    confidence=AssertionConfidence.DIRECTLY_OBSERVED,
                    needs_review=has_error,
                    enabled=True,
                    description=f"Verify '{action.selector.display()}' checked={expected_state}",
                    selector=action.selector,
                    expected=expected_state,
                    after_action_id=action.id,
                    evidence_summary=event.summary,
                    raw_event_ids=list(event.raw_event_ids),
                )
            )
            next_order += 1

        # 2. Navigation outcome -> URL assertion (correlated if paired with network/DOM)
        if nav_outcomes:
            last_nav = nav_outcomes[-1]
            nav_url = str(last_nav.details.get("url", ""))
            fragment = _url_path_fragment(nav_url)
            if fragment:
                confidence = (
                    AssertionConfidence.CORRELATED
                    if (has_network_ok or dom_outcomes)
                    else AssertionConfidence.DIRECTLY_OBSERVED
                )
                evidence_parts = [last_nav.summary]
                raw_ids = list(last_nav.raw_event_ids)
                if net_outcomes:
                    evidence_parts.append(net_outcomes[0].summary)
                    raw_ids.extend(net_outcomes[0].raw_event_ids)
                assertions.append(
                    TestAssertion(
                        order_index=next_order,
                        assertion_type=AssertionType.URL_CONTAINS,
                        confidence=confidence,
                        needs_review=has_error,
                        enabled=True,
                        description=f"Verify URL reaches '{fragment}'",
                        expected=fragment,
                        after_action_id=action.id,
                        evidence_summary="; ".join(evidence_parts),
                        raw_event_ids=dedupe_preserve(raw_ids),
                    )
                )
                next_order += 1

        # 3. Notable DOM changes -> visible text / element assertion (strong direct evidence)
        dom_assertion = self._build_dom_assertion(
            dom_outcomes,
            action_id=action.id,
            order_index=next_order,
            has_error=has_error,
        )
        if dom_assertion is not None:
            assertions.append(dom_assertion)
            next_order += 1

        # 4. Network-only outcome without navigation or visible DOM confirmation:
        #    NEVER treat HTTP 200 alone as proof of workflow success!
        #    Emit an inferred, disabled-by-default review item so the user decides.
        if net_outcomes and not nav_outcomes and dom_assertion is None:
            first_net = net_outcomes[0]
            status = first_net.details.get("status", "")
            method = first_net.details.get("method", "GET")
            url_frag = _url_path_fragment(str(first_net.details.get("url", "")))
            assertions.append(
                TestAssertion(
                    order_index=next_order,
                    assertion_type=AssertionType.NETWORK_RESPONSE,
                    confidence=AssertionConfidence.INFERRED,
                    needs_review=True,
                    enabled=False,
                    description=(
                        f"Review: observed {method} {status} {url_frag} without visible UI change"
                    ).strip(),
                    expected=f"{status} {url_frag}".strip(),
                    after_action_id=action.id,
                    evidence_summary=(
                        f"{first_net.summary} — HTTP response alone is not proof of UI success; "
                        "confirm or replace with a visible UI assertion."
                    ),
                    raw_event_ids=list(first_net.raw_event_ids),
                )
            )

        return assertions

    @staticmethod
    def _build_dom_assertion(
        dom_outcomes: Sequence[CorrelatedOutcome],
        *,
        action_id: str,
        order_index: int,
        has_error: bool,
    ) -> TestAssertion | None:
        for outcome in dom_outcomes:
            notable = outcome.details.get("notable", [])
            if not isinstance(notable, list):
                continue
            for entry in notable:
                if not isinstance(entry, dict):
                    continue
                if entry.get("visible") is False:
                    continue
                text = " ".join(str(entry.get("text", "")).split())
                if text and len(text) <= 120:
                    return TestAssertion(
                        order_index=order_index,
                        assertion_type=AssertionType.TEXT_VISIBLE,
                        confidence=AssertionConfidence.DIRECTLY_OBSERVED,
                        needs_review=has_error,
                        enabled=True,
                        description=f"Verify '{text}' is visible",
                        expected=text,
                        after_action_id=action_id,
                        evidence_summary=outcome.summary,
                        raw_event_ids=list(outcome.raw_event_ids),
                    )
                selector, _ = build_selector_candidates(entry)
                if selector is not None:
                    return TestAssertion(
                        order_index=order_index,
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        confidence=AssertionConfidence.DIRECTLY_OBSERVED,
                        needs_review=has_error,
                        enabled=True,
                        description=f"Verify {selector.display()} is visible",
                        selector=selector,
                        after_action_id=action_id,
                        evidence_summary=outcome.summary,
                        raw_event_ids=list(outcome.raw_event_ids),
                    )
        return None

    @staticmethod
    def _last_observed_url(events: Sequence[ProcessedEvent], *, fallback: str) -> str:
        url = fallback
        for event in events:
            if event.action_type in (ProcessedActionType.NAVIGATE, ProcessedActionType.OPEN_TAB):
                candidate = str(event.payload.get("url", "")).strip()
                if candidate and candidate != "about:blank":
                    url = candidate
            for outcome in event.outcomes:
                if outcome.outcome_type == OutcomeType.NAVIGATION:
                    candidate = str(outcome.details.get("url", "")).strip()
                    if candidate and candidate != "about:blank":
                        url = candidate
        return url

    @staticmethod
    def _unique_func_name(base_name: str, used: set[str]) -> str:
        base = slugify_test_function_name(base_name)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate


def parse_selector_expression(expression: str) -> SelectorCandidate:
    """Parse a user-edited selector string like `test_id=foo`, `text=Save`, or `#btn`."""
    raw = expression.strip()
    if not raw:
        raise ValueError("Selector cannot be empty.")

    prefix_map = {
        "test_id=": SelectorStrategy.TEST_ID,
        "label=": SelectorStrategy.LABEL,
        "placeholder=": SelectorStrategy.PLACEHOLDER,
        "text=": SelectorStrategy.TEXT,
        "css=": SelectorStrategy.CSS,
    }
    for prefix, strategy in prefix_map.items():
        if raw.startswith(prefix):
            val = raw[len(prefix) :].strip()
            if not val:
                raise ValueError(f"Selector value after '{prefix}' cannot be empty.")
            return SelectorCandidate(strategy=strategy, value=val, confidence=0.95)

    if raw.startswith("role="):
        body = raw[len("role=") :].strip()
        if not body:
            raise ValueError("Selector value after 'role=' cannot be empty.")
        if "[name=" in body and body.endswith("]"):
            role_part, name_part = body.split("[name=", 1)
            role_clean = role_part.strip()
            if not role_clean:
                raise ValueError("Role name in 'role=' selector cannot be empty.")
            name_clean = name_part[:-1].strip()
            if len(name_clean) >= 2 and name_clean[0] == name_clean[-1] and name_clean[0] in "'\"":
                try:
                    evaluated = ast.literal_eval(name_clean)
                    name_clean = evaluated if isinstance(evaluated, str) else name_clean[1:-1]
                except (ValueError, SyntaxError):
                    name_clean = name_clean[1:-1]
            return SelectorCandidate(
                strategy=SelectorStrategy.ROLE,
                value=f"{role_clean}:{name_clean}",
                role=role_clean,
                name=name_clean,
                confidence=0.95,
            )
        return SelectorCandidate(
            strategy=SelectorStrategy.ROLE,
            value=body,
            role=body,
            confidence=0.9,
        )

    return SelectorCandidate(strategy=SelectorStrategy.CSS, value=raw, confidence=0.9)


def selector_to_dict(selector: SelectorCandidate | None) -> dict[str, Any] | None:
    """Serialize an optional selector candidate."""
    return selector.model_dump(mode="json") if selector is not None else None
