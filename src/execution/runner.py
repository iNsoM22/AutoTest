"""Managed subprocess Pytest runner with structured progress streaming and failure capture."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.definitions.models import TestDefinition
from src.execution.models import (
    ExecutionMode,
    ExecutionProgressEvent,
    RunStatus,
    TestCaseResult,
    TestResultStatus,
    TestRun,
)
from src.export.service import CopyExportService
from src.storage.artifacts import ArtifactManager, ArtifactRecord, ArtifactRepository

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ExecutionProgressEvent], None]

_EVENT_PREFIX = "__AUTOTEST_EVENT__:"

# Self-contained Pytest plugin written to the run workspace (not part of user-exported suites).
# Streams structured test progress, captures failure screenshots, and collects console/network
# errors from Playwright pages without modifying the generated standalone test code.
_RUNNER_PLUGIN_SOURCE = '''"""AutoTest internal Pytest plugin for structured execution streaming."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_PREFIX = "__AUTOTEST_EVENT__:"
_REPORTS_BY_NODEID: dict[str, dict[str, Any]] = {}
_META_BY_NODEID: dict[str, dict[str, Any]] = {}


def _emit(payload: dict[str, Any]) -> None:
    stream = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
    stream.write(f"\\n{_PREFIX}{json.dumps(payload, ensure_ascii=False)}\\n")
    stream.flush()


def _base_func_name(nodeid: str) -> str:
    raw = nodeid.rsplit("::", 1)[-1]
    return raw.split("[", 1)[0]


@pytest.fixture(autouse=True)
def _autotest_page_observer(request: pytest.FixtureRequest):
    console_errors: list[str] = []
    network_errors: list[str] = []
    page: Any = None

    for fixture_name in ("authenticated_page", "page"):
        if fixture_name in request.fixturenames:
            try:
                page = request.getfixturevalue(fixture_name)
                break
            except Exception:
                page = None

    listeners: list[tuple[str, Any]] = []
    if page is not None:
        def _on_console(msg: Any) -> None:
            try:
                if getattr(msg, "type", "") == "error":
                    console_errors.append(str(msg.text))
            except Exception:
                pass

        def _on_pageerror(err: Any) -> None:
            console_errors.append(f"pageerror: {err}")

        def _on_response(resp: Any) -> None:
            try:
                if int(resp.status) >= 400:
                    network_errors.append(f"{resp.status} {resp.url}")
            except Exception:
                pass

        def _on_requestfailed(req: Any) -> None:
            try:
                failure = getattr(req, "failure", "") or "failed"
                network_errors.append(f"FAILED {req.method} {req.url} ({failure})")
            except Exception:
                pass

        for ev_name, handler in (
            ("console", _on_console),
            ("pageerror", _on_pageerror),
            ("response", _on_response),
            ("requestfailed", _on_requestfailed),
        ):
            try:
                page.on(ev_name, handler)
                listeners.append((ev_name, handler))
            except Exception:
                pass

    yield

    if page is not None and listeners:
        for ev_name, handler in listeners:
            try:
                page.remove_listener(ev_name, handler)
            except Exception:
                pass

    screenshot_path = ""
    failed = bool(getattr(request.node, "_autotest_failed", False))
    screenshot_dir = os.environ.get("AUTOTEST_SCREENSHOT_DIR", "")
    if failed and page is not None and screenshot_dir:
        try:
            out_dir = Path(screenshot_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            func_name = _base_func_name(request.node.nodeid)
            target = out_dir / f"{func_name}.png"
            page.screenshot(path=str(target), full_page=True)
            screenshot_path = str(target)
        except Exception:
            screenshot_path = ""

    _META_BY_NODEID[request.node.nodeid] = {
        "screenshot_path": screenshot_path,
        "console_errors": console_errors,
        "network_errors": network_errors,
    }


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    _REPORTS_BY_NODEID[nodeid] = {}
    _META_BY_NODEID.pop(nodeid, None)
    _emit(
        {
            "type": "test_started",
            "function_name": _base_func_name(nodeid),
        }
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    report = outcome.get_result()
    if report.when in ("setup", "call") and report.failed:
        item._autotest_failed = True


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    bucket = _REPORTS_BY_NODEID.setdefault(report.nodeid, {})
    bucket[report.when] = report


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    reports = _REPORTS_BY_NODEID.pop(nodeid, {})
    meta = _META_BY_NODEID.pop(nodeid, {}) or {}
    rep_setup = reports.get("setup")
    rep_call = reports.get("call")
    rep_teardown = reports.get("teardown")

    status = "passed"
    primary = rep_call or rep_setup or rep_teardown
    duration_s = 0.0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    for rep in (rep_setup, rep_call, rep_teardown):
        if rep is not None:
            duration_s += float(getattr(rep, "duration", 0.0) or 0.0)
            out = str(getattr(rep, "capstdout", "") or "")
            err = str(getattr(rep, "capstderr", "") or "")
            if out:
                stdout_parts.append(out)
            if err:
                stderr_parts.append(err)

    if rep_setup is not None and rep_setup.failed:
        status = "error"
        primary = rep_setup
    elif rep_setup is not None and rep_setup.skipped:
        status = "skipped"
        primary = rep_setup
    elif rep_call is not None and rep_call.failed:
        status = "failed"
        primary = rep_call
    elif rep_call is not None and rep_call.skipped:
        status = "skipped"
        primary = rep_call
    elif rep_teardown is not None and rep_teardown.failed:
        status = "error"
        primary = rep_teardown

    traceback_text = ""
    error_message = ""
    if primary is not None and status in ("failed", "error", "skipped"):
        traceback_text = str(getattr(primary, "longreprtext", "") or "")
        error_message = _extract_error_message(primary, traceback_text)

    _emit(
        {
            "type": "test_finished",
            "function_name": _base_func_name(nodeid),
            "status": status,
            "duration_ms": max(0, int(round(duration_s * 1000))),
            "error_message": error_message,
            "traceback": traceback_text,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
            "screenshot_path": meta.get("screenshot_path", ""),
            "console_errors": meta.get("console_errors", []),
            "network_errors": meta.get("network_errors", []),
        }
    )


def _extract_error_message(report: Any, traceback_text: str) -> str:
    longrepr = getattr(report, "longrepr", None)
    reprcrash = getattr(longrepr, "reprcrash", None)
    if reprcrash is not None and getattr(reprcrash, "message", ""):
        return str(reprcrash.message)
    for line in reversed(traceback_text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("E "):
            return stripped[2:].strip()
    lines = [ln.strip() for ln in traceback_text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""
'''


class ExecutionRunner:
    """Runs generated Playwright + Pytest tests in a managed subprocess with live streaming."""

    def __init__(
        self,
        artifacts: ArtifactManager,
        artifact_repository: ArtifactRepository,
        copy_service: CopyExportService | None = None,
        *,
        python_executable: str | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._artifact_repo = artifact_repository
        self._copy_service = copy_service or CopyExportService()
        self._custom_python = python_executable is not None
        self._python_executable = python_executable or sys.executable

    def _build_pytest_command(self, pytest_args: Sequence[str]) -> list[str]:
        """Build the subprocess command for Pytest (supports frozen PyInstaller binaries)."""
        if not self._custom_python and getattr(sys, "frozen", False):
            return [self._python_executable, "--run-pytest", *pytest_args]
        return [self._python_executable, "-m", "pytest", *pytest_args]

    def execute(
        self,
        run: TestRun,
        definitions: Sequence[TestDefinition],
        results: Sequence[TestCaseResult],
        *,
        base_url: str = "",
        headed: bool = False,
        cancel_event: threading.Event | None = None,
        on_result_updated: Callable[[TestCaseResult], Any] | None = None,
        on_event: ProgressCallback | None = None,
    ) -> tuple[TestRun, list[TestCaseResult]]:
        """Materialize tests, execute Pytest in a subprocess, and collect results & artifacts."""
        run_dir = self._artifacts.ensure_run_dir(run.project_id, run.session_id, run.id)
        suite_dir = run_dir / "suite"
        screenshots_dir = run_dir / "screenshots"
        shared_context = run.mode == ExecutionMode.JOURNEY and len(definitions) > 1
        self._copy_service.export_suite_to_dir(
            definitions,
            suite_dir,
            base_url=base_url,
            shared_browser_context=shared_context,
            include_excluded=True,
        )

        plugin_path = run_dir / "_autotest_runner_plugin.py"
        plugin_path.write_text(_RUNNER_PLUGIN_SOURCE, encoding="utf-8")

        results_by_func: dict[str, TestCaseResult] = {r.function_name: r for r in results}
        result_list = list(results)

        pytest_args = [
            "-c",
            str(suite_dir / "pytest.ini"),
            "--rootdir",
            str(suite_dir),
            "-p",
            "_autotest_runner_plugin",
            "--capture=sys",
            "--tb=short",
            str(suite_dir),
        ]
        if headed:
            pytest_args.append("--headed")
        cmd = self._build_pytest_command(pytest_args)

        env = os.environ.copy()
        existing_pypath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{run_dir}{os.pathsep}{existing_pypath}" if existing_pypath else str(run_dir)
        )
        env["AUTOTEST_SCREENSHOT_DIR"] = str(screenshots_dir)
        env["PYTHONUNBUFFERED"] = "1"

        started_mono = time.monotonic()
        run.status = RunStatus.RUNNING
        self._emit(
            on_event,
            ExecutionProgressEvent(
                event_type="run_started",
                run_id=run.id,
                status=run.status.value,
                message=f"Starting {len(definitions)} test(s) in {run.mode.value} mode...",
                run=run,
            ),
        )

        log_lines: list[str] = []
        cancelled = False
        return_code: int | None = None

        popen_kwargs: dict[str, Any] = {
            "cwd": str(suite_dir),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)

        line_queue: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            assert proc.stdout is not None
            try:
                for raw_line in proc.stdout:
                    line_queue.put(raw_line)
            finally:
                line_queue.put(None)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            eof = False
            while not eof:
                if cancel_event is not None and cancel_event.is_set() and not cancelled:
                    cancelled = True
                    log_lines.append("[AutoTest] Cancellation requested — stopping Pytest...")
                    self._terminate_process(proc)

                try:
                    line = line_queue.get(timeout=0.1)
                except queue.Empty:
                    if proc.poll() is not None and not reader_thread.is_alive():
                        break
                    continue

                if line is None:
                    eof = True
                    break

                stripped = line.rstrip("\r\n")
                if _EVENT_PREFIX in stripped:
                    before, payload_text = stripped.split(_EVENT_PREFIX, 1)
                    if before.strip():
                        log_lines.append(before)
                        self._emit(
                            on_event,
                            ExecutionProgressEvent(
                                event_type="log",
                                run_id=run.id,
                                message=before,
                            ),
                        )
                    self._handle_plugin_event(
                        run,
                        payload_text,
                        results_by_func,
                        on_result_updated=on_result_updated,
                        on_event=on_event,
                    )
                else:
                    log_lines.append(stripped)
                    self._emit(
                        on_event,
                        ExecutionProgressEvent(
                            event_type="log",
                            run_id=run.id,
                            message=stripped,
                        ),
                    )

            reader_thread.join(timeout=2.0)
            try:
                return_code = proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._terminate_process(proc)
                return_code = proc.poll()
        finally:
            if proc.stdout is not None:
                with contextlib.suppress(Exception):
                    proc.stdout.close()

        # Mark any unfinished tests if cancelled or if pytest exited early (e.g. collection error)
        for res in result_list:
            if res.status in (TestResultStatus.PENDING, TestResultStatus.RUNNING):
                if cancelled:
                    res.status = TestResultStatus.SKIPPED
                    res.error_message = "Cancelled by user."
                else:
                    res.status = TestResultStatus.ERROR
                    res.error_message = f"Test did not complete (pytest exit code {return_code})."
                if on_result_updated is not None:
                    on_result_updated(res)

        # Persist full execution log as an artifact
        log_text = "\n".join(log_lines) + ("\n" if log_lines else "")
        log_path, log_rel = self._artifacts.save_run_bytes(
            run.project_id,
            run.session_id,
            run.id,
            "execution.log",
            log_text.encode("utf-8"),
        )
        log_record = self._artifact_repo.create(
            ArtifactRecord(
                project_id=run.project_id,
                session_id=run.session_id,
                artifact_type="execution_log",
                relative_path=log_rel,
                mime_type="text/plain",
                size_bytes=log_path.stat().st_size,
                metadata={"run_id": run.id},
            )
        )

        # Compute final counts and status
        passed = sum(1 for r in result_list if r.status == TestResultStatus.PASSED)
        failed = sum(
            1 for r in result_list if r.status in (TestResultStatus.FAILED, TestResultStatus.ERROR)
        )
        skipped = sum(1 for r in result_list if r.status == TestResultStatus.SKIPPED)

        run.passed_count = passed
        run.failed_count = failed
        run.skipped_count = skipped
        run.total_count = len(result_list)
        run.ended_at = datetime.now(UTC)
        run.duration_ms = max(0, int(round((time.monotonic() - started_mono) * 1000)))
        run.log_artifact_id = log_record.id
        run.summary = {
            "exit_code": return_code,
            "headed": headed,
            "cancelled": cancelled,
        }

        if cancelled:
            run.status = RunStatus.CANCELLED
        elif failed > 0 or (return_code not in (0, 5)):
            run.status = RunStatus.FAILED
        else:
            run.status = RunStatus.PASSED

        self._emit(
            on_event,
            ExecutionProgressEvent(
                event_type="run_finished",
                run_id=run.id,
                status=run.status.value,
                duration_ms=run.duration_ms,
                message=(
                    f"Run {run.status.value}: {passed} passed, {failed} failed, "
                    f"{skipped} skipped ({run.duration_ms / 1000:.2f}s)."
                ),
                run=run,
            ),
        )
        return run, result_list

    def _handle_plugin_event(
        self,
        run: TestRun,
        payload_text: str,
        results_by_func: dict[str, TestCaseResult],
        *,
        on_result_updated: Callable[[TestCaseResult], Any] | None,
        on_event: ProgressCallback | None,
    ) -> None:
        try:
            data: dict[str, Any] = json.loads(payload_text)
        except json.JSONDecodeError:
            logger.debug("Ignoring malformed runner event payload: %s", payload_text)
            return

        event_type = str(data.get("type", ""))
        func_name = str(data.get("function_name", ""))
        result = results_by_func.get(func_name)
        if result is None:
            return

        if event_type == "test_started":
            result.status = TestResultStatus.RUNNING
            if on_result_updated is not None:
                on_result_updated(result)
            self._emit(
                on_event,
                ExecutionProgressEvent(
                    event_type="test_started",
                    run_id=run.id,
                    function_name=func_name,
                    status=result.status.value,
                    result=result,
                ),
            )
            return

        if event_type == "test_finished":
            raw_status = str(data.get("status", "failed"))
            try:
                result.status = TestResultStatus(raw_status)
            except ValueError:
                result.status = TestResultStatus.ERROR
            result.duration_ms = max(0, int(data.get("duration_ms", 0) or 0))
            result.error_message = str(data.get("error_message", ""))
            result.traceback = str(data.get("traceback", ""))
            result.stdout = str(data.get("stdout", ""))
            result.stderr = str(data.get("stderr", ""))
            result.console_errors = [str(x) for x in data.get("console_errors", [])]
            result.network_errors = [str(x) for x in data.get("network_errors", [])]

            screenshot_path_str = str(data.get("screenshot_path", "") or "")
            if screenshot_path_str:
                screenshot_path = Path(screenshot_path_str)
                if screenshot_path.is_file():
                    try:
                        rel_path = self._artifacts.to_relative_path(screenshot_path)
                        artifact = self._artifact_repo.create(
                            ArtifactRecord(
                                project_id=run.project_id,
                                session_id=run.session_id,
                                artifact_type="failure_screenshot",
                                relative_path=rel_path,
                                mime_type="image/png",
                                size_bytes=screenshot_path.stat().st_size,
                                metadata={"run_id": run.id, "function_name": func_name},
                            )
                        )
                        result.screenshot_artifact_id = artifact.id
                    except Exception:
                        logger.debug("Failed to register failure screenshot", exc_info=True)

            if on_result_updated is not None:
                on_result_updated(result)
            self._emit(
                on_event,
                ExecutionProgressEvent(
                    event_type="test_finished",
                    run_id=run.id,
                    function_name=func_name,
                    status=result.status.value,
                    duration_ms=result.duration_ms,
                    message=result.error_message,
                    result=result,
                ),
            )

    @staticmethod
    def _terminate_process(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5.0,
                    check=False,
                )
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                logger.debug("Ignored error terminating Pytest subprocess", exc_info=True)

    @staticmethod
    def _emit(on_event: ProgressCallback | None, event: ExecutionProgressEvent) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            logger.debug("Execution progress callback raised an error", exc_info=True)
