"""Deterministic rule-based generator producing standalone Python + Playwright + Pytest code."""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.definitions.models import (
    AssertionType,
    SelectorCandidate,
    SelectorStrategy,
    TestAction,
    TestAssertion,
    TestDefinition,
    TestMode,
    slugify_test_function_name,
)
from src.generator.models import GeneratedFile, GeneratedSuite
from src.processing.models import ProcessedActionType


def _locator_expr(page_var: str, selector: SelectorCandidate) -> str:
    """Render a Playwright locator expression for a `SelectorCandidate`."""
    if selector.strategy == SelectorStrategy.TEST_ID:
        return f"{page_var}.get_by_test_id({selector.value!r})"
    if selector.strategy == SelectorStrategy.ROLE:
        if selector.name:
            return f"{page_var}.get_by_role({selector.role!r}, name={selector.name!r})"
        return f"{page_var}.get_by_role({selector.role!r})"
    if selector.strategy == SelectorStrategy.LABEL:
        return f"{page_var}.get_by_label({selector.value!r})"
    if selector.strategy == SelectorStrategy.PLACEHOLDER:
        return f"{page_var}.get_by_placeholder({selector.value!r})"
    if selector.strategy == SelectorStrategy.TEXT:
        return f"{page_var}.get_by_text({selector.value!r})"
    return f"{page_var}.locator({selector.value!r})"


def _uses_auth_fixture(definition: TestDefinition) -> bool:
    return any(p.fixture_name == "authenticated_page" for p in definition.prerequisites)


_CTRL_MODIFIER_RE = re.compile(r"(?i)(?:^|(?<=\+))ctrl(?=\+)")


def _normalize_playwright_key(key: str) -> str:
    """Normalize shorthand modifier names (e.g. `Ctrl+a`) to W3C Playwright names (`Control+a`)."""
    return _CTRL_MODIFIER_RE.sub("Control", key)


def _find_auth_setup_definition(definitions: Sequence[TestDefinition]) -> TestDefinition | None:
    """Find the preceding recorded authentication definition for `authenticated_page` setup."""
    auth_consumers = [d for d in definitions if _uses_auth_fixture(d)]
    if not auth_consumers:
        return None
    earliest_consumer_order = min(d.order_index for d in auth_consumers)
    candidates = [
        d
        for d in definitions
        if d.mode == TestMode.INDEPENDENT
        and not _uses_auth_fixture(d)
        and d.order_index < earliest_consumer_order
        and d.actions
    ]
    if not candidates:
        return None
    auth_keywords = ("login", "log in", "sign in", "signin", "authenticate", "auth")
    for cand in candidates:
        text = f"{cand.name} {cand.description}".lower()
        if any(kw in text for kw in auth_keywords) or any(a.is_redacted for a in cand.actions):
            return cand
    return None


def assign_unique_function_names(
    definitions: Sequence[TestDefinition],
) -> list[tuple[str, str]]:
    """Return ``(module_filename, unique_function_name)`` matching ``generate_suite()``."""
    seen_by_module: dict[str, set[str]] = {}
    resolved: list[tuple[str, str]] = []
    for definition in definitions:
        module_file = (
            "test_journey.py" if definition.mode == TestMode.JOURNEY else "test_scenarios.py"
        )
        seen = seen_by_module.setdefault(module_file, set())
        base_name = definition.function_name or slugify_test_function_name(definition.name)
        unique_name = base_name
        suffix = 2
        while unique_name in seen:
            unique_name = f"{base_name}_{suffix}"
            suffix += 1
        seen.add(unique_name)
        resolved.append((module_file, unique_name))
    return resolved


def _safe_doc_text(text: str) -> str:
    """Sanitize arbitrary user text for safe embedding inside triple-quoted docstrings."""
    escaped = text.replace("\\", "\\\\").replace('"""', "'''")
    cleaned = " ".join(escaped.split())
    if cleaned.endswith('"'):
        cleaned = cleaned[:-1] + '\\"'
    return cleaned


def _safe_comment_text(text: str) -> str:
    """Collapse newlines so arbitrary text can never break out of a `# ...` comment."""
    return " ".join(text.splitlines()).strip()


def _has_executable_statement(lines: Sequence[str]) -> bool:
    """Return True if at least one line is a real Python statement (not blank or comment)."""
    return any(line.strip() and not line.strip().startswith("#") for line in lines)


def _needs_os_import(definitions: Sequence[TestDefinition]) -> bool:
    return any(
        a.action_type == ProcessedActionType.FILL and a.is_redacted and a.selector is not None
        for d in definitions
        for a in d.actions
    )


def _needs_re_import(definitions: Sequence[TestDefinition]) -> bool:
    return any(
        a.enabled and a.assertion_type == AssertionType.URL_CONTAINS
        for d in definitions
        for a in d.assertions
    )


_SELECTOR_ASSERTIONS = frozenset(
    {
        AssertionType.ELEMENT_VISIBLE,
        AssertionType.ELEMENT_CHECKED,
        AssertionType.ELEMENT_VALUE,
    }
)


def _needs_expect_import(definitions: Sequence[TestDefinition]) -> bool:
    for d in definitions:
        for a in d.assertions:
            if not a.enabled or a.assertion_type == AssertionType.NETWORK_RESPONSE:
                continue
            if a.assertion_type in _SELECTOR_ASSERTIONS and a.selector is None:
                continue
            return True
    return False


class PytestPlaywrightGenerator:
    """Generates clean, standalone Python + Playwright + Pytest code with zero AutoTest imports."""

    def render_test_function(
        self,
        definition: TestDefinition,
        *,
        function_name: str | None = None,
        shared_browser_context: bool = False,
        suite_viewport: tuple[int, int] | None = None,
    ) -> str:
        """Render a single Pytest test function body (without module-level imports)."""
        page_fixture = "authenticated_page" if _uses_auth_fixture(definition) else "page"
        func_name = (
            function_name or definition.function_name or slugify_test_function_name(definition.name)
        )

        lines: list[str] = [f"def {func_name}({page_fixture}: Page) -> None:"]

        # Docstring with traceability metadata
        safe_name = _safe_doc_text(definition.name)
        doc_lines = [f'    """{safe_name} ({definition.mode.value} test).']
        if definition.description:
            doc_lines.append("")
            doc_lines.append(f"    {_safe_doc_text(definition.description)}")
        if definition.scenario_id:
            doc_lines.append("")
            doc_lines.append(f"    Scenario ID: {_safe_doc_text(definition.scenario_id)}")
        if definition.raw_event_ids:
            preview_ids = ", ".join(definition.raw_event_ids[:8])
            suffix = ", ..." if len(definition.raw_event_ids) > 8 else ""
            doc_lines.append("")
            doc_lines.append(
                f"    Traceability: derived from {len(definition.raw_event_ids)} raw event(s) "
                f"[{preview_ids}{suffix}]."
            )
        doc_lines.append('    """')
        lines.extend(doc_lines)

        body_start = len(lines)
        # Configure per-test timeout (always reset when sharing a context across tests)
        if definition.settings.timeout_ms != 30000 or shared_browser_context:
            lines.append(
                f"    {page_fixture}.set_default_timeout({definition.settings.timeout_ms})"
            )

        # Configure per-test viewport when it differs from the suite-level viewport
        test_viewport = (definition.settings.viewport_width, definition.settings.viewport_height)
        if suite_viewport is not None and test_viewport != suite_viewport:
            lines.append(
                f"    {page_fixture}.set_viewport_size("
                f"{{'width': {test_viewport[0]}, 'height': {test_viewport[1]}}})"
            )

        # Prerequisites (entry URL if test doesn't start with navigation)
        starts_with_nav = bool(
            definition.actions and definition.actions[0].action_type == ProcessedActionType.NAVIGATE
        )
        for prereq in definition.prerequisites:
            if prereq.setup_url and not starts_with_nav:
                lines.append(f"    # Prerequisite: {_safe_comment_text(prereq.description)}")
                if page_fixture == "authenticated_page" or shared_browser_context:
                    expected_url = prereq.setup_url.rstrip("/")
                    lines.append(f"    if {page_fixture}.url.rstrip('/') != {expected_url!r}:")
                    lines.append(f"        {page_fixture}.goto({prereq.setup_url!r})")
                else:
                    lines.append(f"    {page_fixture}.goto({prereq.setup_url!r})")
            elif prereq.fixture_name == "authenticated_page":
                lines.append(f"    # Prerequisite: {_safe_comment_text(prereq.description)}")

        # Group assertions by after_action_id so they appear right after the step they verify
        assertions_by_action: dict[str | None, list[TestAssertion]] = {}
        for assertion in definition.assertions:
            assertions_by_action.setdefault(assertion.after_action_id, []).append(assertion)

        action_lines, active_page, _ = self._render_action_sequence(
            definition.actions,
            page_fixture,
            assertions_by_action=assertions_by_action,
            timeout_ms=definition.settings.timeout_ms,
        )
        lines.extend(action_lines)

        # Remaining detached / end-of-test assertions
        for leftover_group in assertions_by_action.values():
            for assertion in leftover_group:
                lines.extend(
                    self._render_assertion(
                        assertion,
                        active_page,
                        timeout_ms=definition.settings.timeout_ms,
                    )
                )

        if not _has_executable_statement(lines[body_start:]):
            if len(lines) == body_start:
                lines.append("    # No recorded actions in this test definition.")
            lines.append("    pass")

        return "\n".join(lines)

    def render_test_module(
        self,
        definitions: Sequence[TestDefinition],
        *,
        module_doc: str = "Generated end-to-end Playwright + Pytest tests.",
        shared_browser_context: bool = False,
        suite_viewport: tuple[int, int] | None = None,
    ) -> str:
        """Render a complete standalone `.py` test module for one or more definitions."""
        lines: list[str] = [
            f'"""{_safe_doc_text(module_doc)}"""',
            "",
            "from __future__ import annotations",
            "",
        ]

        stdlib_imports: list[str] = []
        if _needs_os_import(definitions):
            stdlib_imports.append("import os")
        if _needs_re_import(definitions):
            stdlib_imports.append("import re")
        if stdlib_imports:
            lines.extend(stdlib_imports)
            lines.append("")

        pw_symbols = ["Page", "expect"] if _needs_expect_import(definitions) else ["Page"]
        lines.append(f"from playwright.sync_api import {', '.join(pw_symbols)}")
        lines.append("")

        seen_names: set[str] = set()
        for idx, definition in enumerate(definitions):
            base_name = definition.function_name or slugify_test_function_name(definition.name)
            unique_name = base_name
            suffix = 2
            while unique_name in seen_names:
                unique_name = f"{base_name}_{suffix}"
                suffix += 1
            seen_names.add(unique_name)
            if idx > 0:
                lines.append("")
            lines.append("")
            lines.append(
                self.render_test_function(
                    definition,
                    function_name=unique_name,
                    shared_browser_context=shared_browser_context,
                    suite_viewport=suite_viewport,
                )
            )

        lines.append("")
        return "\n".join(lines)

    def render_conftest(
        self,
        definitions: Sequence[TestDefinition],
        *,
        base_url: str = "",
        shared_browser_context: bool = False,
        auth_context_definitions: Sequence[TestDefinition] | None = None,
    ) -> GeneratedFile:
        """Render a standalone `conftest.py` with viewport, base URL, and optional auth fixtures."""
        resolved_base = base_url or next(
            (d.settings.base_url for d in definitions if d.settings.base_url),
            "",
        )
        custom_viewport = next(
            (
                (d.settings.viewport_width, d.settings.viewport_height)
                for d in definitions
                if (d.settings.viewport_width, d.settings.viewport_height) != (1280, 720)
            ),
            None,
        )
        if custom_viewport is not None:
            width, height = custom_viewport
        elif definitions:
            width = definitions[0].settings.viewport_width
            height = definitions[0].settings.viewport_height
        else:
            width, height = 1280, 720
        needs_auth = any(_uses_auth_fixture(d) for d in definitions)
        auth_source = auth_context_definitions if auth_context_definitions else definitions
        auth_setup_def = (
            None if shared_browser_context else _find_auth_setup_definition(auth_source)
        )
        needs_browser_import = shared_browser_context or (needs_auth and not shared_browser_context)
        needs_page_import = shared_browser_context or needs_auth

        lines: list[str] = [
            '"""Shared Pytest + Playwright fixtures for generated end-to-end tests."""',
            "",
            "from __future__ import annotations",
            "",
            "import os",
        ]
        if needs_browser_import:
            lines.append("from collections.abc import Generator")
        if needs_auth and not shared_browser_context:
            lines.append("from pathlib import Path")
        lines.extend(
            [
                "from typing import Any",
                "",
                "import pytest",
            ]
        )
        if needs_browser_import:
            lines.append("from playwright.sync_api import Browser, Page")
        elif needs_page_import:
            lines.append("from playwright.sync_api import Page")

        lines.extend(
            [
                "",
                "",
                '@pytest.fixture(scope="session")',
                "def base_url(pytestconfig: pytest.Config) -> str:",
                '    """Base URL (--base-url, BASE_URL env, pytest.ini, or default)."""',
                '    cli_url = pytestconfig.getoption("base_url", default="") or ""',
                "    try:",
                '        ini_url = pytestconfig.getini("base_url") or ""',
                "    except ValueError:",
                '        ini_url = ""',
                '    env_url = os.environ.get("BASE_URL", "")',
                f"    return str(cli_url or env_url or ini_url or {resolved_base!r})",
                "",
                "",
                '@pytest.fixture(scope="session")',
                "def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:",
                '    """Configure consistent viewport dimensions for Playwright contexts."""',
                "    return {",
                "        **browser_context_args,",
                f'        "viewport": {{"width": {width}, "height": {height}}},',
                "    }",
            ]
        )

        if shared_browser_context:
            lines.extend(
                [
                    "",
                    "",
                    '@pytest.fixture(scope="session")',
                    "def _shared_journey_page(",
                    "    browser: Browser,",
                    "    browser_context_args: dict[str, Any],",
                    ") -> Generator[Page, None, None]:",
                    '    """Share one browser context and page across sequential journey tests."""',
                    "    context = browser.new_context(**browser_context_args)",
                    "    shared_page = context.new_page()",
                    "    try:",
                    "        yield shared_page",
                    "    finally:",
                    "        context.close()",
                    "",
                    "",
                    "@pytest.fixture",
                    "def page(_shared_journey_page: Page) -> Page:",
                    '    """Provide the shared sequential journey page to each test."""',
                    "    return _shared_journey_page",
                ]
            )

        if needs_auth:
            if shared_browser_context:
                lines.extend(
                    [
                        "",
                        "",
                        "@pytest.fixture",
                        "def authenticated_page(page: Page) -> Page:",
                        '    """Reuse the shared journey page after earlier auth steps."""',
                        "    return page",
                    ]
                )
            else:
                if auth_setup_def is not None:
                    lines.extend(
                        [
                            "",
                            "",
                            "def _perform_auth_setup(page: Page) -> None:",
                            '    """Replay recorded authentication steps for independent tests."""',
                        ]
                    )
                    starts_with_nav = bool(
                        auth_setup_def.actions
                        and auth_setup_def.actions[0].action_type == ProcessedActionType.NAVIGATE
                    )
                    auth_body_start = len(lines)
                    for prereq in auth_setup_def.prerequisites:
                        if prereq.setup_url and not starts_with_nav:
                            lines.append(f"    page.goto({prereq.setup_url!r})")
                    auth_lines, _, _ = self._render_action_sequence(
                        auth_setup_def.actions,
                        "page",
                    )
                    lines.extend(auth_lines)
                    if not _has_executable_statement(lines[auth_body_start:]):
                        lines.append("    pass")
                lines.extend(
                    [
                        "",
                        "",
                        "@pytest.fixture",
                        "def authenticated_page(",
                        "    browser: Browser,",
                        "    browser_context_args: dict[str, Any],",
                        "    page: Page,",
                        ") -> Generator[Page, None, None]:",
                        '    """Provide an authenticated browser page for independent tests.',
                        "",
                        "    Set TEST_STORAGE_STATE to a Playwright storage_state JSON file",
                        "    to reuse saved cookies/localStorage, or customize this fixture.",
                        '    """',
                        '    state_path = os.environ.get("TEST_STORAGE_STATE", "").strip()',
                        "    if state_path and Path(state_path).is_file():",
                        "        context = browser.new_context(",
                        "            **browser_context_args,",
                        "            storage_state=state_path,",
                        "        )",
                        "        auth_page = context.new_page()",
                        "        try:",
                        "            yield auth_page",
                        "        finally:",
                        "            context.close()",
                        "        return",
                    ]
                )
                if auth_setup_def is not None:
                    lines.append("    _perform_auth_setup(page)")
                lines.append("    yield page")

        lines.append("")
        return GeneratedFile(
            filename="conftest.py",
            content="\n".join(lines),
            description="Shared Pytest fixtures and Playwright browser context configuration.",
        )

    def render_pytest_ini(self, *, base_url: str = "") -> GeneratedFile:
        """Render a minimal standalone `pytest.ini` configuration file."""
        lines = [
            "[pytest]",
            "testpaths = .",
            "addopts = -v",
        ]
        if base_url:
            escaped_base = base_url.replace("%", "%%")
            lines.append(f"base_url = {escaped_base}")
        lines.append("")
        return GeneratedFile(
            filename="pytest.ini",
            content="\n".join(lines),
            description="Standalone Pytest configuration.",
        )

    def generate_suite(
        self,
        definitions: Sequence[TestDefinition],
        *,
        base_url: str = "",
        shared_browser_context: bool = False,
        include_excluded: bool = False,
        auth_context_definitions: Sequence[TestDefinition] | None = None,
    ) -> GeneratedSuite:
        """Generate a complete standalone suite (conftest, pytest.ini, and test modules)."""
        included = list(definitions) if include_excluded else [d for d in definitions if d.included]
        independent = [d for d in included if d.mode == TestMode.INDEPENDENT]
        journeys = [d for d in included if d.mode == TestMode.JOURNEY]

        custom_vp = next(
            (
                (d.settings.viewport_width, d.settings.viewport_height)
                for d in included
                if (d.settings.viewport_width, d.settings.viewport_height) != (1280, 720)
            ),
            None,
        )
        suite_viewport = (
            custom_vp
            if custom_vp is not None
            else (
                (included[0].settings.viewport_width, included[0].settings.viewport_height)
                if included
                else (1280, 720)
            )
        )

        test_files: list[GeneratedFile] = []
        if independent:
            test_files.append(
                GeneratedFile(
                    filename="test_scenarios.py",
                    content=self.render_test_module(
                        independent,
                        module_doc="Independent Playwright + Pytest scenario tests.",
                        shared_browser_context=shared_browser_context,
                        suite_viewport=suite_viewport,
                    ),
                    description=f"{len(independent)} independent scenario test(s).",
                )
            )
        if journeys:
            test_files.append(
                GeneratedFile(
                    filename="test_journey.py",
                    content=self.render_test_module(
                        journeys,
                        module_doc="Complete sequential Playwright + Pytest journey test.",
                        shared_browser_context=shared_browser_context,
                        suite_viewport=suite_viewport,
                    ),
                    description=f"{len(journeys)} sequential journey test(s).",
                )
            )

        return GeneratedSuite(
            conftest=self.render_conftest(
                included,
                base_url=base_url,
                shared_browser_context=shared_browser_context,
                auth_context_definitions=auth_context_definitions,
            ),
            pytest_ini=self.render_pytest_ini(base_url=base_url),
            test_files=test_files,
        )

    def _render_action_sequence(
        self,
        actions: Sequence[TestAction],
        base_page_var: str,
        *,
        assertions_by_action: dict[str | None, list[TestAssertion]] | None = None,
        timeout_ms: int = 30000,
    ) -> tuple[list[str], str, bool]:
        """Render actions (and paired assertions) while tracking an active-tab page stack."""
        lines: list[str] = []
        page_stack: list[str] = [base_page_var]
        tab_counter = 0
        emitted_body = False

        for action in actions:
            emitted_body = True
            current_page = page_stack[-1]
            if action.action_type == ProcessedActionType.OPEN_TAB:
                tab_counter += 1
                new_var = "new_page" if tab_counter == 1 else f"new_page_{tab_counter}"
                lines.append(f"    {new_var} = {current_page}.context.new_page()")
                if action.url and action.url != "about:blank":
                    lines.append(f"    {new_var}.goto({action.url!r})")
                page_stack.append(new_var)
            elif action.action_type == ProcessedActionType.CLOSE_TAB:
                if len(page_stack) > 1:
                    closed_var = page_stack.pop()
                    lines.append(f"    {closed_var}.close()")
                else:
                    lines.append(f"    # Recorded tab close: {_safe_comment_text(action.summary)}")
            else:
                lines.extend(self._render_action(action, current_page))

            if assertions_by_action is not None:
                for assertion in assertions_by_action.pop(action.id, []):
                    lines.extend(
                        self._render_assertion(assertion, page_stack[-1], timeout_ms=timeout_ms)
                    )

        return lines, page_stack[-1], emitted_body

    def _render_action(
        self,
        action: TestAction,
        page_var: str,
        *,
        has_opened_tab: bool = False,
    ) -> list[str]:
        lines: list[str] = []
        atype = action.action_type

        if atype == ProcessedActionType.NAVIGATE:
            if action.url:
                lines.append(f"    {page_var}.goto({action.url!r})")
            return lines

        if atype == ProcessedActionType.OPEN_TAB:
            lines.append(f"    new_page = {page_var}.context.new_page()")
            if action.url and action.url != "about:blank":
                lines.append(f"    new_page.goto({action.url!r})")
            return lines

        if atype == ProcessedActionType.CLOSE_TAB:
            if has_opened_tab:
                lines.append("    new_page.close()")
            else:
                lines.append(f"    # Recorded tab close: {_safe_comment_text(action.summary)}")
            return lines

        if atype == ProcessedActionType.SCROLL:
            coords = action.value.split(",", 1) if "," in action.value else []
            if len(coords) == 2:
                try:
                    sx, sy = int(coords[0].strip()), int(coords[1].strip())
                except ValueError:
                    lines.append(f"    # {_safe_comment_text(action.summary)}")
                else:
                    lines.append(f"    {page_var}.evaluate('window.scrollTo({sx}, {sy})')")
            else:
                lines.append(f"    # {_safe_comment_text(action.summary)}")
            return lines

        locator = _locator_expr(page_var, action.selector) if action.selector else None
        safe_summary = _safe_comment_text(action.summary)

        if atype == ProcessedActionType.CLICK:
            if locator is not None:
                lines.append(f"    {locator}.click()")
            else:
                lines.append(f"    # TODO: add selector for click step ({safe_summary})")
            return lines

        if atype == ProcessedActionType.FILL:
            if locator is not None:
                if action.is_redacted:
                    env_key = f"TEST_SECRET_STEP_{action.order_index}"
                    lines.append(
                        f"    # Redacted sensitive input; supply {env_key} in environment."
                    )
                    lines.append(f"    {locator}.fill(os.environ.get({env_key!r}, ''))")
                else:
                    lines.append(f"    {locator}.fill({action.value!r})")
            else:
                lines.append(f"    # TODO: add selector for fill step ({safe_summary})")
            return lines

        if atype == ProcessedActionType.SELECT:
            if locator is not None:
                lines.append(f"    {locator}.select_option({action.value!r})")
            else:
                lines.append(f"    # TODO: add selector for select step ({safe_summary})")
            return lines

        if atype == ProcessedActionType.CHECK:
            if locator is not None:
                method = "check" if action.checked is not False else "uncheck"
                lines.append(f"    {locator}.{method}()")
            else:
                lines.append(f"    # TODO: add selector for check step ({safe_summary})")
            return lines

        if atype == ProcessedActionType.SUBMIT:
            if action.key:
                key = _normalize_playwright_key(action.key)
                lines.append(f"    {page_var}.keyboard.press({key!r})")
            elif action.via_form_submit and locator is not None:
                lines.append(f"    {locator}.evaluate('form => form.requestSubmit()')")
            elif locator is not None:
                lines.append(f"    {locator}.click()")
            else:
                lines.append(f"    {page_var}.keyboard.press('Enter')")
            return lines

        if atype == ProcessedActionType.PRESS_KEY:
            key = _normalize_playwright_key(action.key or "Enter")
            if locator is not None:
                lines.append(f"    {locator}.press({key!r})")
            else:
                lines.append(f"    {page_var}.keyboard.press({key!r})")
            return lines

        if atype == ProcessedActionType.HOVER:
            if locator is not None:
                lines.append(f"    {locator}.hover()")
            return lines

        lines.append(
            f"    # Unsupported action '{atype.value}': {_safe_comment_text(action.summary)}"
        )
        return lines

    def _render_assertion(
        self,
        assertion: TestAssertion,
        page_var: str,
        *,
        timeout_ms: int = 30000,
    ) -> list[str]:
        conf = assertion.confidence.value
        safe_desc = _safe_comment_text(assertion.description)
        if not assertion.enabled:
            return [f"    # REVIEW [{conf}] (disabled): {safe_desc}"]

        lines: list[str] = []
        if assertion.needs_review:
            lines.append(f"    # REVIEW [{conf}]: {safe_desc}")
        else:
            lines.append(f"    # Assert [{conf}]: {safe_desc}")

        t_kw = f", timeout={timeout_ms}" if timeout_ms != 30000 else ""
        t_only = f"timeout={timeout_ms}" if timeout_ms != 30000 else ""

        atype = assertion.assertion_type
        if atype == AssertionType.URL_CONTAINS:
            lines.append(
                f"    expect({page_var}).to_have_url("
                f"re.compile(re.escape({assertion.expected!r})){t_kw})"
            )
        elif atype == AssertionType.URL_EQUALS:
            lines.append(f"    expect({page_var}).to_have_url({assertion.expected!r}{t_kw})")
        elif atype == AssertionType.TEXT_VISIBLE:
            lines.append(
                f"    expect({page_var}.get_by_text({assertion.expected!r}))"
                f".to_be_visible({t_only})"
            )
        elif atype == AssertionType.ELEMENT_VISIBLE:
            if assertion.selector is not None:
                loc = _locator_expr(page_var, assertion.selector)
                lines.append(f"    expect({loc}).to_be_visible({t_only})")
            else:
                lines.append(f"    # TODO: missing selector for assertion: {safe_desc}")
        elif atype == AssertionType.ELEMENT_CHECKED:
            if assertion.selector is not None:
                loc = _locator_expr(page_var, assertion.selector)
                checked = assertion.expected.lower() != "false"
                method = "to_be_checked" if checked else "not_to_be_checked"
                lines.append(f"    expect({loc}).{method}({t_only})")
            else:
                lines.append(f"    # TODO: missing selector for assertion: {safe_desc}")
        elif atype == AssertionType.ELEMENT_VALUE:
            if assertion.selector is not None:
                loc = _locator_expr(page_var, assertion.selector)
                lines.append(f"    expect({loc}).to_have_value({assertion.expected!r}{t_kw})")
            else:
                lines.append(f"    # TODO: missing selector for assertion: {safe_desc}")
        elif atype == AssertionType.NETWORK_RESPONSE:
            safe_exp = _safe_comment_text(assertion.expected)
            lines.append(f"    # Network evidence ({safe_exp}): pair with a visible UI assertion.")

        return lines
