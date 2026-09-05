# AutoTest — Desktop E2E Test Recorder & Generator

A cross-platform, Python-first desktop application that launches a dedicated Chromium browser, records user interactions and supporting browser/application evidence, processes observations into reviewable scenarios, and generates executable **Python + Playwright + Pytest** test suites.

## Architecture Overview

- **Desktop UI:** PySide6
- **Browser Automation:** Playwright for Python (dedicated Chromium) + injected JavaScript observers
- **Storage:** SQLite (WAL mode) + JSON event payloads + filesystem artifacts
- **Test Output & Runner:** Standard Python + Playwright + Pytest (independent and sequential journey modes)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package and environment management

## Setup

```bash
# 1. Install dependencies and create the virtual environment
uv sync

# 2. Install Playwright's Chromium browser binary
uv run playwright install chromium

# 3. (Optional) Copy environment template
cp .env.example .env
```

## Common Commands

```bash
# Run the application
uv run autotest

# Run tests
uv run pytest

# Lint and format check
uv run ruff check .
uv run ruff format --check .

# Static type checking
uv run mypy
```

## Project Layout

```text
src/
├── config/       # Application settings, cross-platform paths, and logging
├── ui/           # PySide6 desktop shell and views
├── projects/     # Project CRUD and session lifecycle
├── browser/      # Dedicated Chromium & Playwright controller
├── recording/    # Hybrid recording engine & injected JS observers
├── storage/      # SQLite repositories, migrations, and artifact manager
├── processing/   # Event filtering, debouncing, grouping, and correlation
├── scenarios/    # Scenario detection and editing
├── definitions/  # Editable test definitions, actions, and assertions
├── codegen/      # Rule-based Python + Playwright + Pytest code generator
├── execution/    # Built-in isolated Pytest test runner
└── export/       # Copy/integration service for generated tests
```
