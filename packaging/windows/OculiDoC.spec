# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
)


SPEC_DIRECTORY = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIRECTORY.parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
ENTRY_POINT = (
    SOURCE_ROOT
    / "oculidoc"
    / "__main__.py"
)
ICON_PATH = (
    SOURCE_ROOT
    / "oculidoc"
    / "assets"
    / "app_icon.ico"
)
VERSION_FILE = Path(
    os.environ["OCULIDOC_VERSION_FILE"]
).resolve()

datas = collect_data_files(
    "oculidoc",
    includes=[
        "assets/*.ico",
        "assets/*.png",
    ],
)
hiddenimports = sorted(
    set(
        collect_submodules(
            "oculidoc.tasks"
        )
        + [
            "oculidoc.package_smoke",
        ]
    )
)

analysis = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytestqt",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(
    analysis.pure
)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OculiDoC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
    version=str(VERSION_FILE),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OculiDoC",
)
