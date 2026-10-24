# -*- mode: python ; coding: utf-8 -*-
"""Cross-platform PyInstaller build specification for AutoTest.

Build command (run from the AutoTest/ directory):
    uv sync --group packaging
    uv run pyinstaller packaging/autotest.spec

Supports Windows (.exe), macOS (.app / binary), and Linux (ELF binary).
Playwright's Chromium browser is installed separately or bundled via
PLAYWRIGHT_BROWSERS_PATH; see docs/PACKAGING.md for details.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "src" / "app.py"

hiddenimports = (
    collect_submodules("src")
    + collect_submodules("playwright")
    + ["pytest", "pytest_playwright", "platformdirs", "pydantic_settings"]
)

datas = collect_data_files("playwright")

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
