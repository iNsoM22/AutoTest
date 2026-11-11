"""Copy and export service for standalone Python + Playwright + Pytest snippets and suites."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from src.definitions.models import TestDefinition
from src.generator.pytest_generator import PytestPlaywrightGenerator


class CopyExportService:
    """Produces copy-ready code blocks and exported files for integration into user projects.

    Supports all copy modes specified in Project.md §12.1:
    - An individual test.
    - An entire scenario.
    - Selected test cases.
    - All generated tests.
    - Required fixtures and configuration separately.
    """

    def __init__(self, generator: PytestPlaywrightGenerator | None = None) -> None:
        self._generator = generator or PytestPlaywrightGenerator()

    def copy_single_test(self, definition: TestDefinition) -> str:
        """Render a single self-contained Pytest test module for clipboard copy."""
        return self._generator.render_test_module(
            [definition],
            module_doc=f"Generated Playwright + Pytest test: {definition.name}.",
        )

    def copy_scenario_tests(
        self,
        definitions: Sequence[TestDefinition],
        scenario_id: str,
    ) -> str:
        """Render all test definitions belonging to a single scenario."""
        matched = [d for d in definitions if d.scenario_id == scenario_id]
        if not matched:
            return ""
        return self._generator.render_test_module(
            matched,
            module_doc=f"Generated Playwright + Pytest tests for scenario '{matched[0].name}'.",
        )

    def copy_selected_tests(self, definitions: Sequence[TestDefinition]) -> str:
        """Render a user-selected subset of test definitions."""
        if not definitions:
            return ""
        return self._generator.render_test_module(
            definitions,
            module_doc=f"Selected generated Playwright + Pytest tests ({len(definitions)}).",
        )

    def copy_all_tests(self, definitions: Sequence[TestDefinition]) -> str:
        """Render all included generated tests in execution order."""
        included = [d for d in definitions if d.included]
        if not included:
            return ""
        return self._generator.render_test_module(
            included,
            module_doc=f"All generated Playwright + Pytest tests ({len(included)}).",
        )

    def copy_fixtures_and_config(
        self,
        definitions: Sequence[TestDefinition],
        *,
        base_url: str = "",
    ) -> str:
        """Render supporting `conftest.py` fixtures and `pytest.ini` configuration separately."""
        conftest = self._generator.render_conftest(definitions, base_url=base_url)
        pytest_ini = self._generator.render_pytest_ini(base_url=base_url)
        return (
            f"# ===== {conftest.filename} =====\n"
            f"{conftest.content}\n"
            f"# ===== {pytest_ini.filename} =====\n"
            f"{pytest_ini.content}"
        )

    def export_suite_to_dir(
        self,
        definitions: Sequence[TestDefinition],
        output_dir: Path,
        *,
        base_url: str = "",
        shared_browser_context: bool = False,
        include_excluded: bool = False,
        auth_context_definitions: Sequence[TestDefinition] | None = None,
    ) -> list[Path]:
        """Write a complete standalone Pytest + Playwright suite to `output_dir`."""
        suite = self._generator.generate_suite(
            definitions,
            base_url=base_url,
            shared_browser_context=shared_browser_context,
            include_excluded=include_excluded,
            auth_context_definitions=auth_context_definitions,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for generated in [suite.conftest, suite.pytest_ini, *suite.test_files]:
            path = output_dir / generated.filename
            path.write_text(generated.content, encoding="utf-8")
            written.append(path)
        return written
