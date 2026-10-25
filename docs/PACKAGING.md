# Cross-Platform Packaging Guide

AutoTest is designed to run natively on **Windows**, **macOS**, and **Linux** using Python 3.12+, PySide6, SQLite, Pytest, and Playwright's dedicated Chromium.

## 1. Verify Environment Before Building

Run the built-in environment doctor from the `AutoTest/` directory:

```bash
uv run autotest --doctor
```

Ensure all checks report `[PASS]` and `Status: READY`.

## 2. Build a Desktop Bundle with PyInstaller

Install the optional `packaging` dependency group and build using the shared cross-platform spec:

```bash
uv sync --group packaging
uv run pyinstaller packaging/autotest.spec
```

This produces a self-contained directory bundle in `dist/autotest/`:
- **Windows:** `dist/autotest/autotest.exe`
- **macOS:** `dist/autotest/autotest`
- **Linux:** `dist/autotest/autotest`

## 3. Bundling or Installing Playwright Chromium

Playwright Chromium binaries are ~150 MB per platform and are intentionally kept outside the Python wheel. Choose one of two distribution strategies:

### Option A: Install Chromium on First Setup (Recommended for Developer Teams)
Users run once after installing AutoTest:

```bash
playwright install chromium
```

On Linux, install OS browser dependencies if needed:

```bash
playwright install-deps chromium
```

### Option B: Ship Chromium Alongside the Bundle (Offline / Air-Gapped)
Download Chromium into a bundle-local directory before packaging or zipping:

```bash
# Linux / macOS
PLAYWRIGHT_BROWSERS_PATH=dist/autotest/browsers uv run playwright install chromium

# Windows (PowerShell)
$env:PLAYWRIGHT_BROWSERS_PATH = "dist\autotest\browsers"
uv run playwright install chromium
```

Launch the packaged app with `PLAYWRIGHT_BROWSERS_PATH` pointing to that `browsers` folder.

## 4. Platform-Specific Data Paths

AutoTest uses `platformdirs` so SQLite databases, logs, and artifacts are stored in standard OS locations by default (overridable via `AUTOTEST_DATA_DIR`):

| OS | Default Data Directory |
|---|---|
| Windows | `%LOCALAPPDATA%\AutoTest\AutoTest` |
| macOS | `~/Library/Application Support/AutoTest` |
| Linux | `~/.local/share/AutoTest` |
