"""Live Chromium reproducer for replaying processed recording steps."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright
from pydantic import BaseModel, Field

from src.definitions.models import SelectorCandidate, SelectorStrategy
from src.definitions.selectors import build_selector_candidates
from src.generator.pytest_generator import _normalize_playwright_key
from src.processing.models import ProcessedActionType, ProcessedEvent

logger = logging.getLogger(__name__)


class ReproductionStepResult(BaseModel):
    """Outcome of replaying a single processed recording step."""

    order_index: int
    action_type: str
    description: str
    status: str  # "passed" | "failed" | "skipped"
    error_message: str = ""
    duration_ms: int = 0


class ReproductionSummary(BaseModel):
    """Aggregate summary of a processed recording reproduction run."""

    session_id: str
    total_steps: int = 0
    passed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    cancelled: bool = False
    steps: list[ReproductionStepResult] = Field(default_factory=list)


StepCallback = Callable[[ReproductionStepResult], None]


class ProcessedRecordingReproducer:
    """Replays `ProcessedEvent` steps directly in a dedicated Chromium browser."""

    def reproduce(
        self,
        session_id: str,
        events: Sequence[ProcessedEvent],
        *,
        start_url: str = "",
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
        slow_mo_ms: int = 250,
        step_timeout_ms: int = 10_000,
        hold_open_ms: int = 1500,
        cancel_event: threading.Event | None = None,
        on_step: StepCallback | None = None,
    ) -> ReproductionSummary:
        """Launch Chromium and replay every processed step in sequence."""
        summary = ReproductionSummary(session_id=session_id, total_steps=len(events))
        if not events:
            return summary

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
            try:
                context = browser.new_context(
                    viewport={"width": viewport_width, "height": viewport_height}
                )
                context.set_default_timeout(step_timeout_ms)
                page_stack: list[Page] = [context.new_page()]
                current_page = page_stack[-1]

                first_is_nav = events[0].action_type == ProcessedActionType.NAVIGATE
                initial_url = start_url.strip()
                if initial_url and initial_url != "about:blank" and not first_is_nav:
                    current_page.goto(initial_url, wait_until="domcontentloaded")

                for ev in events:
                    if cancel_event is not None and cancel_event.is_set():
                        summary.cancelled = True
                        break

                    t0 = time.monotonic()
                    step_res: ReproductionStepResult
                    try:
                        status, note, current_page = self._replay_step(
                            current_page, page_stack, ev, step_timeout_ms
                        )
                        dur_ms = max(0, int((time.monotonic() - t0) * 1000))
                        step_res = ReproductionStepResult(
                            order_index=ev.order_index,
                            action_type=ev.action_type.value,
                            description=ev.summary,
                            status=status,
                            error_message=note,
                            duration_ms=dur_ms,
                        )
                    except Exception as exc:
                        dur_ms = max(0, int((time.monotonic() - t0) * 1000))
                        step_res = ReproductionStepResult(
                            order_index=ev.order_index,
                            action_type=ev.action_type.value,
                            description=ev.summary,
                            status="failed",
                            error_message=str(exc),
                            duration_ms=dur_ms,
                        )

                    summary.steps.append(step_res)
                    if step_res.status == "passed":
                        summary.passed_steps += 1
                    elif step_res.status == "skipped":
                        summary.skipped_steps += 1
                    else:
                        summary.failed_steps += 1

                    if on_step is not None:
                        on_step(step_res)

                if (
                    not headless
                    and hold_open_ms > 0
                    and (cancel_event is None or not cancel_event.is_set())
                ):
                    with suppress(PlaywrightError):
                        current_page.wait_for_timeout(hold_open_ms)
                with suppress(PlaywrightError):
                    context.close()
            finally:
                with suppress(PlaywrightError):
                    browser.close()

        return summary

    def _replay_step(
        self,
        page: Page,
        page_stack: list[Page],
        event: ProcessedEvent,
        timeout_ms: int,
    ) -> tuple[str, str, Page]:
        action = event.action_type
        payload = event.payload

        if action == ProcessedActionType.NAVIGATE:
            nav_type = str(payload.get("navigation_type", "") or "").lower()
            target_url = str(payload.get("url", "") or "").strip()
            if nav_type == "reload":
                page.reload(wait_until="domcontentloaded")
            elif target_url:
                page.goto(target_url, wait_until="domcontentloaded")
            else:
                return "skipped", "No target URL recorded.", page
            return "passed", "", page

        if action == ProcessedActionType.OPEN_TAB:
            new_page = page.context.new_page()
            page_stack.append(new_page)
            target_url = str(payload.get("url", "") or "").strip()
            if target_url and target_url != "about:blank":
                new_page.goto(target_url, wait_until="domcontentloaded")
            return "passed", "", new_page

        if action == ProcessedActionType.CLOSE_TAB:
            if len(page_stack) > 1:
                closing = page_stack.pop()
                with suppress(PlaywrightError):
                    closing.close()
                return "passed", "", page_stack[-1]
            return "skipped", "Cannot close the last open tab.", page

        if action == ProcessedActionType.SCROLL:
            x, y = 0, 0
            with suppress(ValueError, TypeError):
                x = int(payload.get("scroll_x", 0) or 0)
                y = int(payload.get("scroll_y", 0) or 0)
            page.evaluate("([x, y]) => window.scrollTo(x, y)", [x, y])
            return "passed", "", page

        if action == ProcessedActionType.PRESS_KEY:
            raw_key = str(payload.get("key", "") or payload.get("value", "") or "")
            key = _normalize_playwright_key(raw_key)
            if not key:
                return "skipped", "No key recorded.", page
            page.keyboard.press(key)
            return "passed", "", page

        locator = self._resolve_locator(page, event.target, timeout_ms)
        if locator is None:
            return "skipped", "No resolvable selector for target element.", page

        if action == ProcessedActionType.CLICK:
            locator.click()
            return "passed", "", page

        if action == ProcessedActionType.FILL:
            value = str(payload.get("value", "") or "")
            if value == "<REDACTED>" or bool(payload.get("redacted")):
                return "skipped", "Skipped redacted sensitive value.", page
            locator.fill(value)
            return "passed", "", page

        if action == ProcessedActionType.SELECT:
            value = str(payload.get("value", "") or "")
            if value == "<REDACTED>" or bool(payload.get("redacted")):
                return "skipped", "Skipped redacted select value.", page
            selected_text = str(payload.get("selected_text", "") or "").strip()
            if value:
                locator.select_option(value)
            elif selected_text:
                locator.select_option(label=selected_text)
            else:
                return "skipped", "No select option recorded.", page
            return "passed", "", page

        if action == ProcessedActionType.CHECK:
            should_check = payload.get("checked", True)
            if should_check is False:
                locator.uncheck()
            else:
                locator.check()
            return "passed", "", page

        if action == ProcessedActionType.SUBMIT:
            tag = str(event.target.get("tag", "") or "").lower()
            if tag == "form":
                locator.evaluate("form => form.requestSubmit()")
            else:
                locator.click()
            return "passed", "", page

        if action == ProcessedActionType.HOVER:
            locator.hover()
            return "passed", "", page

        return "skipped", f"Unsupported action type: {action.value}", page

    @staticmethod
    def _resolve_locator(
        page: Page,
        target: dict[str, Any],
        timeout_ms: int,
    ) -> Locator | None:
        primary, fallbacks = build_selector_candidates(target)
        candidates: list[SelectorCandidate] = []
        if primary is not None:
            candidates.append(primary)
        candidates.extend(fallbacks)
        if not candidates:
            return None

        # Try candidates in stability order; pick the first that resolves to a visible element.
        for candidate in candidates:
            loc = _locator_from_candidate(page, candidate)
            if loc is None:
                continue
            try:
                if loc.first.count() > 0:
                    return loc.first
            except PlaywrightError:
                continue

        # Fall back to the primary candidate so Playwright's auto-waiting handles dynamic UI.
        return _locator_from_candidate(page, candidates[0])


def _locator_from_candidate(page: Page, candidate: SelectorCandidate) -> Locator | None:
    if candidate.strategy == SelectorStrategy.TEST_ID:
        return page.get_by_test_id(candidate.value)
    if candidate.strategy == SelectorStrategy.ROLE:
        if candidate.name:
            return page.get_by_role(candidate.role, name=candidate.name)  # type: ignore[arg-type]
        return page.get_by_role(candidate.role)  # type: ignore[arg-type]
    if candidate.strategy == SelectorStrategy.LABEL:
        return page.get_by_label(candidate.value)
    if candidate.strategy == SelectorStrategy.PLACEHOLDER:
        return page.get_by_placeholder(candidate.value)
    if candidate.strategy == SelectorStrategy.TEXT:
        return page.get_by_text(candidate.value)
    if candidate.strategy == SelectorStrategy.CSS:
        return page.locator(candidate.value)
    return None
