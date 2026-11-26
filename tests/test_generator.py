"""Tests for standalone Pytest + Playwright code generation and copy/export service."""

from __future__ import annotations

import ast
from pathlib import Path

from src.definitions import (
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
)
from src.export import CopyExportService
from src.generator import PytestPlaywrightGenerator
from src.processing import ProcessedActionType


def _sample_definitions() -> list[TestDefinition]:
    a1 = TestAction(
        id="act-1",
        order_index=1,
        action_type=ProcessedActionType.NAVIGATE,
        summary="Navigate to https://app.example.com/login",
        url="https://app.example.com/login",
        raw_event_ids=["r1"],
    )
    a2 = TestAction(
        id="act-2",
        order_index=2,
        action_type=ProcessedActionType.FILL,
        summary="Fill 'Email' with 'alice@example.com'",
        selector=SelectorCandidate(strategy=SelectorStrategy.LABEL, value="Email", confidence=0.88),
        value="alice@example.com",
        raw_event_ids=["r2"],
    )
    a3 = TestAction(
        id="act-3",
        order_index=3,
        action_type=ProcessedActionType.FILL,
        summary="Fill 'Password' with '[REDACTED]'",
        selector=SelectorCandidate(
            strategy=SelectorStrategy.CSS, value='input[name="password"]', confidence=0.76
        ),
        value="",
        is_redacted=True,
        raw_event_ids=["r3"],
    )
    a4 = TestAction(
        id="act-4",
        order_index=4,
        action_type=ProcessedActionType.CLICK,
        summary="Click 'Sign in'",
        selector=SelectorCandidate(
            strategy=SelectorStrategy.ROLE,
            value="button:Sign in",
            role="button",
            name="Sign in",
            confidence=0.92,
        ),
        raw_event_ids=["r4"],
    )

    login_def = TestDefinition(
        id="def-1",
        project_id="p1",
        session_id="s1",
        scenario_id="sc-1",
        name="User Login",
        function_name="test_user_login",
        description="Authenticate with valid credentials.",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[a1, a2, a3, a4],
        assertions=[
            TestAssertion(
                id="as-1",
                order_index=1,
                assertion_type=AssertionType.URL_CONTAINS,
                confidence=AssertionConfidence.CORRELATED,
                enabled=True,
                description="Verify URL reaches '/dashboard'",
                expected="/dashboard",
                after_action_id="act-4",
                raw_event_ids=["r5"],
            ),
            TestAssertion(
                id="as-2",
                order_index=2,
                assertion_type=AssertionType.TEXT_VISIBLE,
                confidence=AssertionConfidence.DIRECTLY_OBSERVED,
                enabled=True,
                description="Verify 'Dashboard' is visible",
                expected="Dashboard",
                after_action_id="act-4",
                raw_event_ids=["r6"],
            ),
        ],
        settings=TestExecutionSettings(base_url="https://app.example.com"),
        raw_event_ids=["r1", "r2", "r3", "r4", "r5", "r6"],
    )

    sync_def = TestDefinition(
        id="def-2",
        project_id="p1",
        session_id="s1",
        scenario_id="sc-2",
        name="Sync Data",
        function_name="test_sync_data",
        mode=TestMode.INDEPENDENT,
        order_index=2,
        prerequisites=[
            TestPrerequisite(
                name="entry_url",
                description="Open dashboard first.",
                fixture_name="page",
                setup_url="https://app.example.com/dashboard",
            ),
            TestPrerequisite(
                name="authenticated_session",
                description="Requires authenticated session.",
                fixture_name="authenticated_page",
            ),
        ],
        actions=[
            TestAction(
                id="act-5",
                order_index=1,
                action_type=ProcessedActionType.CLICK,
                summary="Click 'Sync'",
                selector=SelectorCandidate(
                    strategy=SelectorStrategy.TEST_ID, value="sync-btn", confidence=0.98
                ),
                raw_event_ids=["r7"],
            )
        ],
        assertions=[
            TestAssertion(
                id="as-3",
                order_index=1,
                assertion_type=AssertionType.NETWORK_RESPONSE,
                confidence=AssertionConfidence.INFERRED,
                needs_review=True,
                enabled=False,
                description="Review: observed POST 200 /api/sync without visible UI change",
                expected="200 /api/sync",
                after_action_id="act-5",
                raw_event_ids=["r8"],
            )
        ],
        settings=TestExecutionSettings(base_url="https://app.example.com"),
        raw_event_ids=["r7", "r8"],
    )

    journey_def = TestDefinition(
        id="def-3",
        project_id="p1",
        session_id="s1",
        scenario_id=None,
        name="Complete Recorded Journey",
        function_name="test_complete_recorded_journey",
        mode=TestMode.JOURNEY,
        order_index=3,
        actions=[a1, a4],
        assertions=[],
        settings=TestExecutionSettings(base_url="https://app.example.com"),
        raw_event_ids=["r1", "r4"],
    )
    return [login_def, sync_def, journey_def]


def test_generated_suite_is_valid_standalone_python_with_zero_autotest_imports() -> None:
    definitions = _sample_definitions()
    generator = PytestPlaywrightGenerator()
    suite = generator.generate_suite(definitions, base_url="https://app.example.com")

    # Every Python file must parse cleanly and have zero AutoTest/src imports
    py_files = [suite.conftest, *suite.test_files]
    assert len(suite.test_files) == 2  # test_scenarios.py + test_journey.py

    for generated in py_files:
        ast.parse(generated.content)
        assert "from src" not in generated.content
        assert "import src" not in generated.content
        assert "autotest" not in generated.content.lower()

    scenarios_code = suite.test_files[0].content
    assert "os.environ.get('TEST_SECRET_STEP_3', '')" in scenarios_code
    assert "[REDACTED]" not in scenarios_code
    assert "def test_sync_data(authenticated_page: Page) -> None:" in scenarios_code
    assert "REVIEW [inferred] (disabled):" in scenarios_code
    assert "def authenticated_page(" in suite.conftest.content
    assert "TEST_STORAGE_STATE" in suite.conftest.content
    assert "def _perform_auth_setup(page: Page) -> None:" in suite.conftest.content


def test_copy_export_service_supports_all_copy_modes_and_disk_export(tmp_path: Path) -> None:
    definitions = _sample_definitions()
    svc = CopyExportService()

    single = svc.copy_single_test(definitions[0])
    ast.parse(single)
    assert "def test_user_login(" in single
    assert "def test_sync_data(" not in single

    scenario_code = svc.copy_scenario_tests(definitions, "sc-2")
    ast.parse(scenario_code)
    assert "def test_sync_data(" in scenario_code
    assert "def test_user_login(" not in scenario_code

    selected_code = svc.copy_selected_tests([definitions[0], definitions[2]])
    ast.parse(selected_code)
    assert "def test_user_login(" in selected_code
    assert "def test_complete_recorded_journey(" in selected_code
    assert "def test_sync_data(" not in selected_code

    all_code = svc.copy_all_tests(definitions)
    ast.parse(all_code)
    assert "def test_user_login(" in all_code
    assert "def test_sync_data(" in all_code
    assert "def test_complete_recorded_journey(" in all_code

    fixtures_and_cfg = svc.copy_fixtures_and_config(definitions, base_url="https://app.example.com")
    assert "conftest.py" in fixtures_and_cfg
    assert "pytest.ini" in fixtures_and_cfg
    assert "browser_context_args" in fixtures_and_cfg

    written = svc.export_suite_to_dir(
        definitions, tmp_path / "exported_suite", base_url="https://app.example.com"
    )
    names = {p.name for p in written}
    assert names == {"conftest.py", "pytest.ini", "test_scenarios.py", "test_journey.py"}
    for path in written:
        assert path.is_file() and path.stat().st_size > 0


def test_generator_sanitizes_tricky_docstrings_and_quotes() -> None:
    defn = TestDefinition(
        project_id="p1",
        session_id="s1",
        name='Checkout """Edge""" Flow\\',
        function_name="test_checkout_edge_flow",
        description='Line 1\nLine 2 with """quotes"""\\',
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[],
        assertions=[],
    )
    code = PytestPlaywrightGenerator().render_test_module([defn])
    ast.parse(code)
    assert "import os" not in code


def test_generator_emits_valid_python_for_comment_only_actions_and_auth_setup() -> None:
    comment_only_auth = TestDefinition(
        id="def-auth",
        project_id="p1",
        session_id="s1",
        scenario_id="sc-1",
        name="User Login",
        function_name="test_user_login",
        mode=TestMode.INDEPENDENT,
        order_index=1,
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.CLICK,
                summary="Click missing selector",
                selector=None,
            ),
            TestAction(
                order_index=2,
                action_type=ProcessedActionType.HOVER,
                summary="Hover missing selector",
                selector=None,
            ),
        ],
    )
    downstream = TestDefinition(
        id="def-next",
        project_id="p1",
        session_id="s1",
        scenario_id="sc-2",
        name="Dashboard Action",
        function_name="test_dashboard_action",
        mode=TestMode.INDEPENDENT,
        order_index=2,
        prerequisites=[
            TestPrerequisite(
                name="authenticated_session",
                description="Requires auth",
                fixture_name="authenticated_page",
            )
        ],
        actions=[
            TestAction(
                order_index=1,
                action_type=ProcessedActionType.NAVIGATE,
                summary="Empty navigate",
                url="",
            )
        ],
    )
    suite = PytestPlaywrightGenerator().generate_suite([comment_only_auth, downstream])
    ast.parse(suite.conftest.content)
    for generated in suite.test_files:
        ast.parse(generated.content)
        assert "    pass" in generated.content
