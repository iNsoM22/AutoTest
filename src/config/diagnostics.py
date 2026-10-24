"""Cross-platform runtime environment diagnostics for AutoTest."""

from __future__ import annotations

import importlib.util
import platform
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import AppSettings


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """Result of a single runtime environment check."""

    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Aggregated cross-platform environment diagnostic report."""

    platform_summary: str
    python_version: str
    checks: tuple[DiagnosticCheck, ...]

    @property
    def ok(self) -> bool:
        """Return True if all required checks succeeded."""
        return all(c.ok for c in self.checks if c.required)

    def format_text(self) -> str:
        """Render a human-readable CLI diagnostic report."""
        lines = [
            f"AutoTest Environment Doctor ({self.platform_summary}, Python {self.python_version})",
            "-" * 72,
        ]
        for check in self.checks:
            marker = "PASS" if check.ok else ("FAIL" if check.required else "WARN")
            lines.append(f"[{marker}] {check.name}: {check.detail}")
        lines.append("-" * 72)
        lines.append("Status: READY" if self.ok else "Status: ACTION REQUIRED")
        return "\n".join(lines)


def _check_module(name: str, module_name: str) -> DiagnosticCheck:
    found = importlib.util.find_spec(module_name) is not None
    detail = f"{module_name} importable" if found else f"{module_name} not installed"
    return DiagnosticCheck(name=name, ok=found, detail=detail)


def _check_writable_dir(name: str, path: Path) -> DiagnosticCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".autotest_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return DiagnosticCheck(name=name, ok=True, detail=str(path))
    except OSError as exc:
        return DiagnosticCheck(name=name, ok=False, detail=f"{path} ({exc})")


def _check_playwright_chromium() -> DiagnosticCheck:
    if importlib.util.find_spec("playwright") is None:
        return DiagnosticCheck(
            name="Playwright Chromium",
            ok=False,
            detail="playwright package not installed",
        )
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            exe_path = Path(pw.chromium.executable_path)
        if exe_path.is_file():
            return DiagnosticCheck(name="Playwright Chromium", ok=True, detail=str(exe_path))
        return DiagnosticCheck(
            name="Playwright Chromium",
            ok=False,
            detail=(
                f"Chromium binary missing at {exe_path}. Run: uv run playwright install chromium"
            ),
        )
    except Exception as exc:
        return DiagnosticCheck(
            name="Playwright Chromium",
            ok=False,
            detail=f"Unable to locate Chromium ({exc}). Run: uv run playwright install chromium",
        )


def run_environment_diagnostics(
    settings: AppSettings,
    *,
    check_browser: bool = True,
) -> DiagnosticReport:
    """Inspect Python, SQLite, storage paths, PySide6, Pytest, and Playwright Chromium."""
    checks: list[DiagnosticCheck] = [
        DiagnosticCheck(
            name="Python Runtime",
            ok=sys.version_info >= (3, 12),
            detail=platform.python_version(),
        ),
        DiagnosticCheck(
            name="SQLite Engine",
            ok=True,
            detail=f"sqlite3 {sqlite3.sqlite_version}",
        ),
        _check_writable_dir("Data Directory", settings.resolved_data_dir),
        _check_writable_dir("Artifacts Directory", settings.artifacts_dir),
        _check_writable_dir("Logs Directory", settings.logs_dir),
        _check_module("PySide6 Desktop UI", "PySide6"),
        _check_module("Pytest Runner", "pytest"),
        _check_module("pytest-playwright Plugin", "pytest_playwright"),
    ]
    if check_browser:
        checks.append(_check_playwright_chromium())

    return DiagnosticReport(
        platform_summary=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python_version=platform.python_version(),
        checks=tuple(checks),
    )
