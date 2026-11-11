# -*- mode: python ; coding: utf-8 -*-
"""Cross-platform PyInstaller build specification for AutoTest.

Build command (run from the AutoTest/ directory):
    uv sync --group packaging
    uv run pyinstaller packaging/autotest.spec

Supports Windows (.exe), macOS (.app / binary), and Linux (ELF binary).
Playwright's Chromium browser is installed separately or bundled via
PLAYWRIGHT_BROWSERS_PATH; see docs/PACKAGING.md for details.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(SPECPATH).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "src" / "app.py"

hiddenimports = (
    collect_submodules("src")
    + collect_submodules("playwright")
    + collect_submodules("pytest")
    + collect_submodules("_pytest")
    + collect_submodules("pytest_playwright")
    + collect_submodules("pytest_base_url")
    + ["platformdirs", "pydantic_settings"]
)

datas = (
    collect_data_files("playwright")
    + [(str(PROJECT_ROOT / "src" / "recording" / "page_observer.js"), "src/recording")]
    + copy_metadata("pytest")
    + copy_metadata("pytest-playwright")
    + copy_metadata("pytest-base-url")
)

a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autotest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="autotest",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AutoTest.app",
        bundle_identifier="com.autotest.desktop",
    )
