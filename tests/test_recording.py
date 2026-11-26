"""Tests for unified event models, redaction, batched storage, and hybrid Chromium recording."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.app import AppContext, create_app_context
from src.browser import BrowserCallbacks, BrowserLaunchConfig, BrowserManager, PageInfo
from src.config import AppSettings
from src.projects import ProjectCreate
from src.recording import (
    REDACTED_VALUE,
    EventCollector,
    EventSource,
    EventType,
    RecordingEngine,
    redact_target_and_payload,
    redact_url,
)
from src.storage import ArtifactRecord

_TEST_HTML = """<!doctype html>
<html>
  <head><title>AutoTest Recording Fixture</title></head>
  <body>
    <form id="login-form" action="/login" method="post">
      <input id="username" name="username" type="text" placeholder="Username" />
      <input id="password" name="password" type="password" placeholder="Password" />
      <select id="role" name="role">
        <option value="user">User</option>
        <option value="admin">Admin</option>
      </select>
      <button id="submit-btn" type="submit" data-testid="login-submit">Sign In</button>
    </form>
    <button id="error-btn" type="button">Trigger Diagnostics</button>
    <div id="status-area"></div>
    <script>
      document.getElementById("login-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        await fetch("/api/login?token=supersecret_token_value", { method: "POST" });
        const heading = document.createElement("h2");
        heading.setAttribute("role", "status");
        heading.textContent = "Welcome back!";
        document.getElementById("status-area").appendChild(heading);
      });
      document.getElementById("error-btn").addEventListener("click", () => {
        console.error("Simulated console error");
        setTimeout(() => { throw new Error("Simulated uncaught exception"); }, 10);
      });
    </script>
  </body>
</html>
"""


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _TEST_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        body = b'{"ok": true, "secret_body": "must_not_be_recorded"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def fixture_server_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def app_context(tmp_settings: AppSettings) -> Iterator[AppContext]:
    ctx = create_app_context(tmp_settings)
    try:
        yield ctx
    finally:
        ctx.close()


def test_redaction_masks_passwords_tokens_and_urls() -> None:
    target, payload = redact_target_and_payload(
        {"tag": "input", "input_type": "password", "name": "user_pwd"},
        {"value": "MySecretPassword123!"},
    )
    assert target["input_type"] == "password"
    assert payload["value"] == REDACTED_VALUE
    assert payload["redacted"] is True

    _, header_payload = redact_target_and_payload(
        {},
        {
            "api_key": "sk-12345",
            "note": "Authorization: Bearer abc.def.ghi",
            "url": "https://user:pass@example.com/api?token=xyz&page=2",
        },
    )
    assert header_payload["api_key"] == REDACTED_VALUE
    assert "abc.def.ghi" not in header_payload["note"]
    assert REDACTED_VALUE in header_payload["note"]
    assert "pass@" not in header_payload["url"]
    assert "xyz" not in header_payload["url"]
    assert "page=2" in header_payload["url"]
    assert redact_url("about:blank") == "about:blank"

    # Associated <label> text triggers redaction even if input has no sensitive name/id
    _, label_payload = redact_target_and_payload(
        {"tag": "input", "input_type": "text", "label": "Master Password"},
        {"value": "LabelSecret42!"},
    )
    assert label_payload["value"] == REDACTED_VALUE
    assert label_payload["redacted"] is True


def test_collector_batches_events_and_artifact_repository_persists(
    app_context: AppContext,
) -> None:
    project = app_context.project_service.create_project(ProjectCreate(name="Shop"))
    session = app_context.session_service.create_session(project.id, start_url="https://shop.test")

    collector = EventCollector(
        app_context.event_repository,
        project_id=project.id,
        session_id=session.id,
        batch_size=3,
    )

    collector.record(event_type=EventType.CLICK, source=EventSource.PAGE_OBSERVER)
    collector.record(event_type=EventType.INPUT, source=EventSource.PAGE_OBSERVER)
    assert collector.buffered_count == 2
    assert app_context.event_repository.count_by_session(session.id) == 0

    # Third event triggers automatic batch flush
    collector.record(event_type=EventType.SUBMIT, source=EventSource.PAGE_OBSERVER)
    assert collector.buffered_count == 0
    assert app_context.event_repository.count_by_session(session.id) == 3

    # Fourth event stays buffered until explicit flush
    collector.record(event_type=EventType.NAVIGATION, source=EventSource.PLAYWRIGHT)
    assert collector.buffered_count == 1
    assert collector.flush() == 1
    assert app_context.event_repository.count_by_session(session.id) == 4

    events = app_context.event_repository.list_by_session(session.id)
    assert [e.sequence_no for e in events] == [1, 2, 3, 4]
    assert (
        len(app_context.event_repository.list_by_session(session.id, event_type=EventType.CLICK))
        == 1
    )

    # Verify artifact file save + SQLite metadata row
    saved_path, rel_path = app_context.artifacts.save_session_bytes(
        project.id, session.id, "trace.txt", b"hello artifact"
    )
    record = app_context.artifact_repository.create(
        ArtifactRecord(
            project_id=project.id,
            session_id=session.id,
            artifact_type="log",
            relative_path=rel_path,
            mime_type="text/plain",
            size_bytes=saved_path.stat().st_size,
        )
    )
    stored_artifacts = app_context.artifact_repository.list_by_session(session.id)
    assert len(stored_artifacts) == 1
    assert stored_artifacts[0].id == record.id
    assert app_context.artifacts.resolve_path(stored_artifacts[0].relative_path).read_bytes() == (
        b"hello artifact"
    )


@pytest.mark.browser
def test_hybrid_recording_captures_interactions_network_dom_and_redacts_secrets(
    app_context: AppContext,
    fixture_server_url: str,
) -> None:
    project = app_context.project_service.create_project(
        ProjectCreate(name="Auth App", base_url=fixture_server_url)
    )
    session = app_context.session_service.create_session(project.id, start_url=fixture_server_url)
    app_context.session_service.start_session(session.id)

    manager_ref: list[BrowserManager] = []
    engine = RecordingEngine(
        app_context.event_repository,
        project_id=project.id,
        session_id=session.id,
        page_id_resolver=lambda p: manager_ref[0].get_page_id(p) if manager_ref else None,
        batch_size=10,
    )

    def on_page_opened(info: PageInfo) -> None:
        app_context.session_service.register_page(
            session.id,
            page_id=info.page_id,
            tab_index=info.tab_index,
            url=info.url,
            title=info.title,
        )
        engine.on_page_opened(info)

    def on_page_navigated(info: PageInfo) -> None:
        app_context.session_service.update_page(info.page_id, url=info.url, title=info.title)
        engine.on_page_navigated(info)

    def on_page_closed(info: PageInfo) -> None:
        app_context.session_service.close_page(info.page_id)
        engine.on_page_closed(info)

    manager = BrowserManager(
        callbacks=BrowserCallbacks(
            on_context_created=engine.attach_context,
            on_page_opened=on_page_opened,
            on_page_attached=engine.attach_page,
            on_page_navigated=on_page_navigated,
            on_page_closed=on_page_closed,
        )
    )
    manager_ref.append(manager)

    try:
        config = BrowserLaunchConfig(headless=True, start_url=fixture_server_url)
        manager.launch(config)
        page = manager._first_open_page()
        assert page is not None

        page.fill("#username", "alice")
        page.fill("#password", "TopSecretPass99!")
        page.select_option("#role", "admin")
        page.click("#submit-btn")
        page.wait_for_selector("h2")
        page.click("#error-btn")
        page.wait_for_timeout(400)
    finally:
        manager.close()
        engine.flush()

    events = app_context.event_repository.list_by_session(session.id)
    event_types = {e.event_type for e in events}

    # Core hybrid observations present
    assert EventType.TAB_OPEN in event_types
    assert EventType.NAVIGATION in event_types
    assert EventType.INPUT in event_types
    assert EventType.CHANGE in event_types
    assert EventType.CLICK in event_types
    assert EventType.SUBMIT in event_types
    assert EventType.DOM_MUTATION in event_types
    assert EventType.REQUEST in event_types
    assert EventType.RESPONSE in event_types
    assert EventType.CONSOLE in event_types
    assert EventType.PAGE_ERROR in event_types
    assert EventType.TAB_CLOSE in event_types

    # Sequence numbers strictly monotonic
    seq_nos = [e.sequence_no for e in events]
    assert seq_nos == list(range(1, len(events) + 1))

    # Password and secret tokens never persisted; response body not persisted
    serialized_all = " ".join(f"{e.target} {e.payload}" for e in events)
    assert "TopSecretPass99!" not in serialized_all
    assert "supersecret_token_value" not in serialized_all
    assert "must_not_be_recorded" not in serialized_all

    password_inputs = [
        e
        for e in events
        if e.event_type in {EventType.INPUT, EventType.CHANGE} and e.target.get("id") == "password"
    ]
    assert password_inputs
    assert all(e.payload.get("value") == REDACTED_VALUE for e in password_inputs)

    # Request and response share a correlation_id for /api/login
    api_requests = [
        e
        for e in events
        if e.event_type == EventType.REQUEST and "/api/login" in e.payload.get("url", "")
    ]
    api_responses = [
        e
        for e in events
        if e.event_type == EventType.RESPONSE and "/api/login" in e.payload.get("url", "")
    ]
    assert len(api_requests) == 1
    assert len(api_responses) == 1
    assert api_requests[0].correlation_id is not None
    assert api_requests[0].correlation_id == api_responses[0].correlation_id
    assert api_responses[0].payload["status"] == 200
