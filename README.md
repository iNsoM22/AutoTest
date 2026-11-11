# AutoTest — Desktop E2E Test Recorder & Generator

A cross-platform, Python-first desktop application that launches a dedicated Chromium browser, records user interactions and supporting browser/application evidence, processes observations into reviewable scenarios, generates editable standalone **Python + Playwright + Pytest** test suites, and executes them inside a built-in isolated runner.

## Architecture Overview

- **Desktop UI:** PySide6 (`MainWindow` with 6 workflow tabs)
- **Browser Automation:** Playwright for Python (dedicated Chromium) + injected JavaScript observers
- **Storage:** SQLite (WAL mode, versioned migrations) + JSON event payloads + filesystem artifacts
- **Three-Layer Data Model:**
  1. **Raw Evidence (`raw_events`):** Immutable captured interactions, DOM changes, network calls, console logs, and page errors (with automatic sensitive-data redaction).
  2. **Interpretation (`processed_events`, `scenarios`):** Filtered, debounced, outcome-correlated timeline and editable scenarios.
  3. **Test Definitions & Code (`test_definitions`):** Editable actions, ranked selectors, evidence-linked assertions, and standalone Playwright + Pytest generation.
- **Built-in Runner:** Managed subprocess Pytest execution supporting **Independent** and **Sequential Journey** modes, live streaming progress, failure screenshots, console/network diagnostics, execution history, and reruns.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package and environment management

## Setup

```bash
# 1. Install dependencies and create the virtual environment
uv sync

# 2. Install Playwright's Chromium browser binary
uv run playwright install chromium

# 3. Verify your cross-platform runtime environment
uv run autotest --doctor

# 4. (Optional) Copy environment template
cp .env.example .env
```

## Common Commands

```bash
# Launch the desktop application
uv run autotest

# Verify database initialization & migrations without launching the GUI
uv run autotest --check

# Run environment diagnostics (Python, SQLite, PySide6, Pytest, Chromium)
uv run autotest --doctor

# Run the full test suite
uv run pytest

# Lint, format check, and static type checking
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Trying the Sample Demo App

AutoTest ships with a self-contained demo web app in [`examples/sample_demo_app/`](examples/sample_demo_app/README.md) featuring a login form, task manager, and search filter with semantic `data-testid` and ARIA attributes. Open `examples/sample_demo_app/index.html` via its `file://` URL (or `python -m http.server`) as your AutoTest project's Start URL to try the full Record -> Review -> Generate -> Execute workflow immediately.

## Cross-Platform Packaging

See [`docs/PACKAGING.md`](docs/PACKAGING.md) for building standalone Windows, macOS, and Linux desktop bundles with PyInstaller (`packaging/autotest.spec`).

## Project Layout

```text
AutoTest/
├── docs/
│   └── PACKAGING.md      # Cross-platform packaging & Chromium bundling guide
├── examples/
│   └── sample_demo_app/  # Self-contained demo web app for trying AutoTest
├── packaging/
│   └── autotest.spec     # Cross-platform PyInstaller build spec
├── src/
│   ├── config/           # Settings, cross-platform paths, logging, and --doctor
│   ├── ui/               # PySide6 desktop shell, review, definitions, and execution tabs
│   ├── projects/         # Project CRUD, session lifecycle, and crash recovery
│   ├── browser/          # Dedicated Chromium & Playwright controller
│   ├── recording/        # Hybrid recording engine, JS observers, and redaction
│   ├── storage/          # SQLite repositories, migrations, and artifact manager
│   ├── processing/       # Event filtering, debouncing, grouping, and correlation
│   ├── scenarios/        # Scenario detection and editing
│   ├── definitions/      # Editable test definitions, selector ranker, and assertions
│   ├── generator/        # Standalone Python + Playwright + Pytest code generator
│   ├── execution/        # Built-in subprocess Pytest runner and execution history
│   └── export/           # 5-mode clipboard copy & suite disk export service
└── tests/                # Unit, browser, GUI, execution, and high-volume perf tests
```
